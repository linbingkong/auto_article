import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wechat_agent import web_app
from wechat_agent.article_access import ArticleAccessStore
from wechat_agent.audit import AuditStore
from wechat_agent.auth import AuthError
from wechat_agent.billing import BillingStore
from wechat_agent.user import UserStore

ROOT = Path(__file__).resolve().parents[1] / "output" / "_user_delete_test"


@pytest.fixture()
def env():
    root = ROOT / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    return root, UserStore(root / "auth.db")


def test_delete_user_store_rules(env):
    _, users = env
    admin = users.create_user("adminx", "password123", role="admin")
    member = users.create_user("member1", "password123", role="creator")
    assert users.delete_user(member["id"])["username"] == "member1"
    assert users.get_user(member["id"]) is None
    with pytest.raises(AuthError) as exc:
        users.delete_user(admin["id"])
    assert exc.value.code == "LAST_ADMIN_PROTECTED"
    with pytest.raises(AuthError) as exc:
        users.delete_user("missing-user")
    assert exc.value.code == "USER_NOT_FOUND"


def test_update_user_phone_backfill_and_conflict(env):
    _, users = env
    admin = users.create_user("adminx", "password123", role="admin")
    member = users.create_user("member3", "password123", role="creator")
    updated = users.update_user(member["id"], phone="138 0013 8000")
    assert updated["phone_last4"] == "8000"
    assert users.get_user(member["id"])["phone"] == "13800138000"
    listing = users.list_users()
    assert next(u for u in listing if u["id"] == member["id"])["phone"] == "13800138000"
    with pytest.raises(AuthError) as exc:
        users.update_user(admin["id"], phone="13800138000")
    assert exc.value.code == "PHONE_EXISTS"
    with pytest.raises(AuthError) as exc:
        users.update_user(member["id"], phone="12345")
    assert exc.value.code == "INVALID_PHONE"
    unchanged = users.update_user(member["id"], phone="   ")
    assert unchanged["phone_last4"] == "8000"


def test_delete_user_api_cleanup_and_reassign(env, monkeypatch):
    root, users = env
    admin = users.create_user("adminx", "password123", role="admin")
    member = users.create_user("member2", "password123", role="creator")
    access = ArticleAccessStore(root / "auth.db")
    access.set_grant("a" * 32, member["id"], ["view"], admin["id"])

    class StubApiConfig:
        def __init__(self):
            self.cleared = []

        def clear(self, user_id):
            self.cleared.append(user_id)

    class StubArticleStore:
        def __init__(self):
            self.calls = []

        def reassign_owner(self, from_id, to_id, username=""):
            self.calls.append((from_id, to_id, username))
            return 3

    api_config = StubApiConfig()
    articles = StubArticleStore()
    billing = BillingStore(root / "auth.db")
    billing.ensure_account(member["id"], 0)
    monkeypatch.setattr(web_app, "user_store", users)
    monkeypatch.setattr(web_app, "audit_store", AuditStore(root / "auth.db"))
    monkeypatch.setattr(web_app, "billing_store", billing)
    monkeypatch.setattr(web_app, "article_access_store", access)
    monkeypatch.setattr(web_app, "user_api_config_store", api_config)
    monkeypatch.setattr(web_app, "article_store", articles)
    client = TestClient(web_app.app)
    login = client.post("/api/auth/login", json={"username": "adminx", "password": "password123"}).json()["data"]
    headers = {"Authorization": "Bearer " + login["access_token"]}
    denied = client.delete(f"/api/users/{admin['id']}", headers=headers)
    assert denied.status_code == 400
    assert denied.json()["code"] == "SELF_DELETE_FORBIDDEN"
    response = client.delete(f"/api/users/{member['id']}", headers=headers)
    assert response.status_code == 200
    assert response.json()["data"]["reassigned_articles"] == 3
    assert articles.calls == [(member["id"], admin["id"], "adminx")]
    assert api_config.cleared == [member["id"]]
    assert access.user_grant("a" * 32, member["id"]) == {}
    assert users.get_user(member["id"]) is None
    # 删除用户后额度快照一并清理，避免遗留孤儿账本行
    with billing._connect() as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) AS cnt FROM user_generation_accounts WHERE user_id=?", (member["id"],)
        ).fetchone()["cnt"]
    assert remaining == 0
