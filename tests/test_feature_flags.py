import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wechat_agent import web_app
from wechat_agent.audit import AuditStore
from wechat_agent.billing import BillingStore
from wechat_agent.config import Config
from wechat_agent.user import UserStore
from wechat_agent.web_core import ConfigService
from wechat_agent.web_models import ConfigUpdateRequest

ROOT = Path(__file__).resolve().parents[1] / "output" / "_feature_flags_test"


@pytest.fixture()
def client(monkeypatch):
    root = ROOT / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    users = UserStore(root / "auth.db")
    users.create_user("adminx", "password123", role="admin")
    users.create_user("creator1", "password123", role="creator")
    cfg = Config()
    cfg.features.user_api_config = False
    monkeypatch.setattr(web_app, "user_store", users)
    monkeypatch.setattr(web_app, "audit_store", AuditStore(root / "auth.db"))
    monkeypatch.setattr(web_app, "billing_store", BillingStore(root / "auth.db"))
    monkeypatch.setattr(web_app.config_service, "load", lambda: cfg)
    c = TestClient(web_app.app)

    def login(username):
        r = c.post("/api/auth/login", json={"username": username, "password": "password123"}).json()["data"]
        return {"Authorization": "Bearer " + r["access_token"]}

    return c, cfg, login


def test_personal_api_flag_gates_user_endpoints(client):
    c, cfg, login = client
    admin_headers = login("adminx")
    creator_headers = login("creator1")
    requests = [
        ("GET", "/api/me/api-config", None),
        ("PUT", "/api/me/api-config", {"llm": {"api_key": "sk-test"}}),
        ("DELETE", "/api/me/api-config/llm", None),
        ("POST", "/api/me/api-config/test/llm", None),
    ]
    for method, url, payload in requests:
        kwargs = {"headers": creator_headers}
        if payload is not None:
            kwargs["json"] = payload
        response = c.request(method, url, **kwargs)
        assert response.status_code == 403
        assert response.json()["code"] == "PERSONAL_API_DISABLED"
    assert c.get("/api/me/api-config", headers=admin_headers).status_code == 200
    me = c.get("/api/auth/me", headers=creator_headers).json()["data"]
    assert me["features"]["user_api_config"] is False
    generate = c.post(
        "/api/generate",
        headers=creator_headers,
        json={"topic": {"title": "测试选题", "source": "manual"}, "options": {"generation_mode": "personal"}},
    )
    assert generate.status_code == 400
    assert generate.json()["code"] == "PERSONAL_API_DISABLED"
    cfg.features.user_api_config = True
    assert c.get("/api/me/api-config", headers=creator_headers).status_code == 200


def test_config_service_roundtrip_features():
    root = ROOT / ("config-" + uuid.uuid4().hex)
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / "config.yaml"
    config_path.write_text("features:\n  user_api_config: true\n", encoding="utf-8")
    service = ConfigService(config_path, root / ".env")
    service.save(ConfigUpdateRequest(features={"user_api_config": False}))
    assert service.load().features.user_api_config is False
