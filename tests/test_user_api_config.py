import sqlite3
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wechat_agent.audit import AuditStore
from wechat_agent.auth import AuthError
from wechat_agent.config import Config
from wechat_agent.user import UserStore
from wechat_agent.user_api_config import UserApiConfigStore
from wechat_agent import web_app


TEST_ROOT = Path(__file__).resolve().parents[1] / "output" / "_user_api_config_test"


@pytest.fixture()
def config_store(monkeypatch):
    path = TEST_ROOT / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("USER_CONFIG_SECRET", "test-user-config-secret-at-least-32-characters")
    return UserApiConfigStore(path / "auth.db")


def test_personal_api_keys_are_encrypted_at_rest(config_store):
    secret_key = "sk-personal-super-secret"
    snapshot = config_store.update(
        "user-a",
        {"llm": {"base_url": "https://llm.example/v1", "model": "model-a", "api_key": secret_key}},
    )
    assert snapshot["services"]["llm"]["api_key"]["configured"] is True
    assert secret_key not in str(snapshot)
    with sqlite3.connect(str(config_store.db_path)) as conn:
        encrypted = conn.execute(
            "SELECT encrypted_payload FROM user_api_configs WHERE user_id='user-a'"
        ).fetchone()[0]
    assert secret_key not in encrypted
    assert "llm.example" not in encrypted


def test_configs_are_isolated_between_users(config_store):
    config_store.update(
        "user-a",
        {"search": {"base_url": "https://api.tavily.com", "api_key": "tvly-user-a", "search_depth": "advanced"}},
    )
    assert config_store.status("user-a")["search"] is True
    assert config_store.status("user-b")["search"] is False
    assert config_store.get("user-b") == {}


def test_editor_requires_personal_config_and_admin_can_fallback(config_store):
    base = Config()
    base.llm.api_key = "system-key"
    with pytest.raises(AuthError) as exc:
        config_store.effective_config(base, {"id": "editor-a", "role": "editor"}, ["llm"])
    assert exc.value.code == "USER_LLM_CONFIG_REQUIRED"

    admin_cfg, sources = config_store.effective_config(
        base, {"id": "admin-a", "role": "admin"}, ["llm"]
    )
    assert admin_cfg.llm.api_key == "system-key"
    assert sources["llm"] == "system"


def test_personal_config_overrides_system_without_leaking_to_other_user(config_store):
    base = Config()
    base.llm.api_key = "system-key"
    config_store.update(
        "editor-a",
        {"llm": {"base_url": "https://personal.example/v1", "model": "personal-model", "api_key": "personal-key"}},
    )
    cfg, sources = config_store.effective_config(
        base, {"id": "editor-a", "role": "editor"}, ["llm"]
    )
    assert cfg.llm.api_key == "personal-key"
    assert cfg.llm.model == "personal-model"
    assert sources["llm"] == "personal"
    assert base.llm.api_key == "system-key"


def test_clear_service_keeps_other_personal_services(config_store):
    config_store.update(
        "user-a",
        {
            "llm": {"base_url": "https://llm.example/v1", "model": "m", "api_key": "llm-key"},
            "search": {"base_url": "https://api.tavily.com", "api_key": "search-key"},
        },
    )
    config_store.clear("user-a", "llm")
    assert config_store.status("user-a")["llm"] is False
    assert config_store.status("user-a")["search"] is True


def test_editor_api_requires_and_uses_own_llm_config(config_store, monkeypatch):
    db_path = config_store.db_path
    users = UserStore(db_path)
    editor = users.create_user("editorapi", "password123", role="editor")
    users.create_user("adminapi2", "password123", role="admin")
    cfg = Config(); cfg.llm.api_key = "system-admin-key"
    monkeypatch.setattr(web_app, "user_store", users)
    monkeypatch.setattr(web_app, "user_api_config_store", config_store)
    monkeypatch.setattr(web_app, "audit_store", AuditStore(db_path))
    monkeypatch.setattr(web_app.config_service, "load", lambda: cfg)
    monkeypatch.setattr(web_app.article_store, "generate", lambda body, config, progress, owner_user=None: {"article_id": "a" * 32, "key": config.llm.api_key})
    client = TestClient(web_app.app)
    login = client.post("/api/auth/login", json={"username": "editorapi", "password": "password123"}).json()["data"]
    headers = {"Authorization": "Bearer " + login["access_token"]}
    request = {"topic": {"title": "个人配置测试", "source": "manual", "score": 80}, "options": {"with_images": False, "style": "deep"}}
    denied = client.post("/api/generate", headers=headers, json=request)
    assert denied.status_code == 400
    assert denied.json()["code"] == "USER_LLM_CONFIG_REQUIRED"
    saved = client.put("/api/me/api-config", headers=headers, json={
        "llm": {"base_url": "https://personal.example/v1", "model": "m", "api_key": "personal-only-key"},
        "search": {"base_url": "https://api.tavily.com", "api_key": "personal-search-key"},
    })
    assert saved.status_code == 200
    assert "personal-only-key" not in saved.text
    accepted = client.post("/api/generate", headers=headers, json=request)
    assert accepted.status_code == 200
    admin_login = client.post("/api/auth/login", json={"username": "adminapi2", "password": "password123"}).json()["data"]
    admin_headers = {"Authorization": "Bearer " + admin_login["access_token"]}
    listed = client.get("/api/users", headers=admin_headers).json()["data"]["users"]
    status = next(item["api_config"] for item in listed if item["id"] == editor["id"])
    assert status["llm"] is True
    assert "personal-only-key" not in str(listed)


def test_personal_token_plan_image_source_is_normalized(config_store):
    snapshot = config_store.update("user-image", {"image": {
        "source": "bailian_token_plan",
        "api_key": "sk-sp-personal-demo",
        "model": "qwen-image-2.0",
    }})
    image = snapshot["services"]["image"]
    assert image["image_source"] == "custom"
    assert image["provider"] == "auto"
    assert "token-plan." in image["base_url"]
    assert image["api_key"]["configured"] is True

    cfg, sources = config_store.effective_config(
        Config(), {"id": "user-image", "role": "creator"}, ["image"]
    )
    assert cfg.image.source == "custom"
    assert cfg.image.api_key == "sk-sp-personal-demo"
    assert sources["image"] == "personal"


def test_personal_custom_source_without_base_url_is_rejected(config_store):
    with pytest.raises(AuthError) as exc:
        config_store.update("user-image", {"image": {
            "source": "custom",
            "api_key": "image-key",
            "model": "qwen-image-2.0",
        }})
    assert exc.value.code == "USER_IMAGE_CONFIG_INCOMPATIBLE"
    assert "Base URL" in str(exc.value)


def test_switching_personal_custom_address_requires_new_key(config_store):
    config_store.update("user-image", {"image": {
        "source": "custom", "base_url": "https://images.one.example/v1",
        "api_key": "image-key", "model": "image-model"
    }})
    with pytest.raises(AuthError) as exc:
        config_store.update("user-image", {"image": {
            "source": "custom", "base_url": "https://images.two.example/v1",
            "model": "image-model"
        }})
    assert exc.value.code == "USER_IMAGE_KEY_REQUIRED"
