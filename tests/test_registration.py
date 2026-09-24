import shutil
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wechat_agent.audit import AuditStore
from wechat_agent.auth import AuthError
from wechat_agent.billing import BillingStore
from wechat_agent.config import Config
from wechat_agent.user import UserStore
from wechat_agent import web_app

ROOT = Path(__file__).resolve().parents[1]
TEST_DIR = ROOT / "output" / "_registration_test"

@pytest.fixture()
def store(monkeypatch):
    path = TEST_DIR / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PHONE_HASH_SECRET", "registration-test-secret-at-least-32-characters")
    result = UserStore(path / "auth.db")
    result.test_dir = path
    return result

def test_registration_stores_plaintext_and_approval_state_machine(store):
    registration = store.create_registration("reader1", "password123", "138 0013 8000")
    assert registration["status"] == "pending_approval"
    assert registration["masked_phone"] == "*******8000"
    assert "phone" not in registration
    row = store.get_user(registration["id"])
    assert row["phone_hash"] != "13800138000"
    assert row["phone"] == "13800138000"
    pending = store.list_registrations("pending_approval")
    assert pending[0]["phone"] == "13800138000"
    assert pending[0]["masked_phone"] == "*******8000"
    with pytest.raises(AuthError) as exc:
        store.authenticate("reader1", "password123")
    assert exc.value.code == "PENDING_APPROVAL"
    assert store.find_registration_by_phone("+86 13800138000")["id"] == registration["id"]
    approved = store.approve_registration(registration["id"], "admin-id", role="viewer")
    assert approved["status"] == "active"
    assert store.authenticate("reader1", "password123")["role"] == "viewer"

def test_public_registration_cannot_be_admin_and_last_admin_is_protected(store):
    registration = store.create_registration("reader2", "password123", "13900139000")
    with pytest.raises(AuthError) as exc:
        store.approve_registration(registration["id"], "admin-id", role="admin")
    assert exc.value.code == "INVALID_ROLE"
    admin = store.create_user("adminx", "password123", role="admin")
    with pytest.raises(AuthError) as exc:
        store.update_user(admin["id"], role="viewer")
    assert exc.value.code == "LAST_ADMIN_PROTECTED"

def test_rejected_registration_can_resubmit(store):
    registration = store.create_registration("reader3", "password123", "13700137000")
    rejected = store.reject_registration(registration["id"], "admin-id", "无法匹配公众号私信")
    assert rejected["status"] == "rejected"
    status = store.registration_status(registration["registration_token"])
    assert status["review_note"] == "无法匹配公众号私信"
    updated = store.resubmit_registration(registration["registration_token"], "13600136000", "newpassword123")
    assert updated["status"] == "pending_approval"
    assert updated["registration_token"] != registration["registration_token"]

def test_registration_api_requires_manual_wechat_confirmation(store, monkeypatch):
    cfg = Config(); cfg.registration.enabled = True; cfg.registration.account_name = "观思辩明"
    monkeypatch.delenv("WEB_ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(web_app, "user_store", store)
    monkeypatch.setattr(web_app, "audit_store", AuditStore(store.test_dir / "audit.db"))
    monkeypatch.setattr(web_app, "billing_store", BillingStore(store.test_dir / "audit.db"))
    monkeypatch.setattr(web_app.config_service, "load", lambda: cfg)
    store.create_user("adminapi", "password123", role="admin")
    client = TestClient(web_app.app)
    login = client.post("/api/auth/login", json={"username":"adminapi","password":"password123"})
    headers = {"Authorization": "Bearer " + login.json()["data"]["access_token"]}
    response = client.post("/api/auth/register", json={"username":"reader4","password":"password123","phone":"13500135000","follow_confirmed":False})
    assert response.status_code == 400
    assert response.json()["code"] == "FOLLOW_NOT_CONFIRMED"
    response = client.post("/api/auth/register", json={"username":"reader4","password":"password123","phone":"13500135000","follow_confirmed":True})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["account_name"] == "观思辩明"
    assert "phone" not in data
    response = client.post(f"/api/registrations/{data['id']}/approve", headers=headers, json={"role":"creator","wechat_phone_verified":False,"note":""})
    assert response.status_code == 400
    assert response.json()["code"] == "WECHAT_PHONE_NOT_VERIFIED"
    response = client.get("/api/registrations", headers=headers, params={"phone":"13500135000"})
    assert response.status_code == 200
    assert response.json()["data"]["items"][0]["username"] == "reader4"
    assert response.json()["data"]["items"][0]["phone"] == "13500135000"
    response = client.post(f"/api/registrations/{data['id']}/approve", headers=headers, json={"role":"creator","wechat_phone_verified":True,"note":"私信已核对"})
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "active"
    assert response.json()["data"]["billing"]["trial_remaining"] == 3
    viewer_login = client.post("/api/auth/login", json={"username":"reader4","password":"password123"})
    viewer_headers = {"Authorization": "Bearer " + viewer_login.json()["data"]["access_token"]}
    forbidden = client.get("/api/registrations", headers=viewer_headers)
    assert forbidden.status_code == 403