import json
import os
import shutil
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wechat_agent.article_store import ArticleStore, _inline_image_specs, _insert_inline_images
from wechat_agent.config import Config
from wechat_agent.history import HistoryStore
from wechat_agent import web_app
from wechat_agent.web_app import app
from wechat_agent.web_core import ConfigService, HotspotService, TaskManager
from wechat_agent.web_models import ConfigUpdateRequest

ROOT = Path(__file__).resolve().parents[1]


def test_web_static_and_health():
    client = TestClient(app)
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["data"]["service"] == "wechat-agent-web"
    assert "clipboard-write" in health.headers["Permissions-Policy"]
    assert client.get("/").status_code == 200
    assert "公众号智能体" in client.get("/").text
    assert client.get("/app.js").status_code == 200
    manual = client.get("/api/help/manual")
    assert manual.status_code == 200
    assert "观思辩明智能体用户使用手册" in manual.json()["data"]["html"]
    assert "/manual-assets/01-login.png" in manual.json()["data"]["html"]
    assert client.get("/manual-assets/01-login.png").status_code == 200
    assert client.get("/manual-assets/not-found.png").status_code == 404


def test_admin_token_protection(monkeypatch):
    monkeypatch.setenv("WEB_ADMIN_TOKEN", "test-admin")
    client = TestClient(app)
    assert client.get("/api/config").status_code == 401
    response = client.get("/api/config", headers={"X-Admin-Token": "test-admin"})
    assert response.status_code == 200
    assert response.json()["ok"] is True
    image = response.json()["data"]["image"]
    assert image["source"] in {"pillow", "custom"}
    assert [item["id"] for item in image["source_catalog"]] == ["pillow", "custom"]


def test_config_service_masks_and_saves_secrets(monkeypatch):
    target = ROOT / "output" / "_web_config_test"
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    config_path = target / "config.yaml"
    env_path = target / ".env"
    config_path.write_text(
        """
llm:
  base_url: https://example.test/v1
  model: demo
  api_key: ${DEEPSEEK_API_KEY}
wechat:
  app_id: ${WECHAT_APP_ID}
  app_secret: ${WECHAT_APP_SECRET}
  publish_mode: draft_only
""",
        encoding="utf-8",
    )
    env_path.write_text("DEEPSEEK_API_KEY=old-secret-key\n", encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "old-secret-key")
    service = ConfigService(config_path, env_path)
    snap = service.snapshot()
    assert snap["llm"]["api_key"]["configured"] is True
    assert "old-secret-key" not in json.dumps(snap)

    service.save(ConfigUpdateRequest(llm={"api_key": "new-super-secret-key"}))
    assert "new-super-secret-key" in env_path.read_text(encoding="utf-8")
    assert "new-super-secret-key" not in config_path.read_text(encoding="utf-8")
    assert service.snapshot()["llm"]["api_key"]["masked"].endswith("-key")

    service.save(ConfigUpdateRequest(search={"api_key": "tvly-secret-key", "search_depth": "basic"}))
    assert "TAVILY_API_KEY=tvly-secret-key" in env_path.read_text(encoding="utf-8")
    assert "tvly-secret-key" not in config_path.read_text(encoding="utf-8")
    assert service.snapshot()["search"]["search_depth"] == "basic"

    service.save(ConfigUpdateRequest(image={"source": "bailian_token_plan", "model": "qwen-image-2.0", "api_key": "sk-sp-image-secret"}))
    image = service.snapshot()["image"]
    assert image["source"] == "custom"
    assert image["provider"] == "auto"
    assert "token-plan." in image["base_url"]
    assert "IMAGE_API_KEY=sk-sp-image-secret" in env_path.read_text(encoding="utf-8")
    assert "sk-sp-image-secret" not in config_path.read_text(encoding="utf-8")

    config_before_invalid_switch = config_path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="必须同时填写"):
        service.save(ConfigUpdateRequest(image={
            "source": "custom", "base_url": "https://images.example.com/v1", "model": "image-model"
        }))
    assert config_path.read_text(encoding="utf-8") == config_before_invalid_switch

    service.save(ConfigUpdateRequest(billing={"wechat_pay_api_v3_key": "12345678901234567890123456789012", "payment_enabled": False}))
    assert "WECHAT_PAY_API_V3_KEY=12345678901234567890123456789012" in env_path.read_text(encoding="utf-8")
    assert "12345678901234567890123456789012" not in config_path.read_text(encoding="utf-8")
    assert service.snapshot()["billing"]["wechat_pay_api_v3_key"]["configured"] is True


def test_inline_images_follow_core_sections_not_intro():
    markdown = """## 引言：背景\n\n引言内容。\n\n## 一、关键事实\n\n第一部分核心内容。\n\n## 二、影响分析\n\n第二部分核心内容。\n\n## 结语：总结\n\n结语内容。"""
    specs = _inline_image_specs(markdown, 2)
    assert [spec["prompt"].splitlines()[0] for spec in specs] == ["一、关键事实", "二、影响分析"]
    result = _insert_inline_images(markdown, specs, [Path("inline_1.png"), Path("inline_2.png")])
    assert not result.startswith("![")
    assert "第一部分核心内容。\n\n![一、关键事实要点图](inline_1.png)" in result
    assert "第二部分核心内容。\n\n![二、影响分析要点图](inline_2.png)" in result


def test_article_store_preview_and_versioning():
    target = ROOT / "output" / "_web_article_test"
    shutil.rmtree(target, ignore_errors=True)
    store = ArticleStore(target / "articles", HistoryStore(target / "history.db"))
    article_id = "a" * 32
    article_dir = target / "articles" / article_id
    article_dir.mkdir(parents=True)
    metadata = {
        "id": article_id,
        "title": "测试标题",
        "digest": "测试摘要",
        "status": "generated",
        "word_count": 4,
        "created_at": "2026-01-01T10:00:00",
        "updated_at": "2026-01-01T10:00:00",
        "version": 1,
        "assets": ["cover.png", "inline_1.png"],
        "topic": {"title": "测试热点", "source": "manual", "score": 80},
    }
    (article_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (article_dir / "article.md").write_text("![图](inline_1.png)\n\n正文", encoding="utf-8")
    (article_dir / "article.html").write_text('<p><img src="inline_1.png"></p>', encoding="utf-8")

    data = store.get(article_id)
    assert f"/api/articles/{article_id}/assets/inline_1.png" in data["preview_html"]
    updated = store.update(article_id, "新标题", "新摘要", "## 新正文")
    assert updated["version"] == 2
    assert updated["title"] == "新标题"
    assert (article_dir / "versions" / "v1.md").exists()
    deleted = store.delete(article_id)
    assert deleted["article_id"] == article_id
    assert not article_dir.exists()


def _wait_task_status(manager, task_id, expected, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = manager.get(task_id)
        if task and task["status"] in expected:
            return task
        time.sleep(0.01)
    raise AssertionError(f"task did not reach {expected}: {manager.get(task_id)}")


def test_hotspot_keyword_filter_matches_title_summary_and_sorts_relevance():
    payload = {
        "items": [
            {"id": "1", "title": "人工智能应用", "summary": "大模型产品更新", "score": 70, "matched_keywords": []},
            {"id": "2", "title": "科技行业动态", "summary": "大模型带动AI工具发展", "score": 80, "matched_keywords": []},
            {"id": "3", "title": "体育新闻", "summary": "赛事信息", "score": 99, "matched_keywords": []},
        ],
        "updated_at": "now",
    }
    result = HotspotService.filter_keywords(payload, "AI，大模型")
    assert [item["id"] for item in result["items"]] == ["2", "1"]
    assert result["items"][0]["matched_query_keywords"] == ["AI", "大模型"]
    assert result["total_before_filter"] == 3
    assert payload["items"][0].get("matched_query_keywords") is None


def test_task_manager_logs_are_timestamped():
    manager = TaskManager(max_workers=1)
    done = threading.Event()

    def quick(progress):
        progress(50, "阶段消息")
        done.set()
        return {"ok": True}

    task_id = manager.submit("generate", quick)
    assert done.wait(2)
    _wait_task_status(manager, task_id, {"success"})
    logs = manager.get(task_id)["logs"]
    assert all(entry.startswith("[") and "]" in entry for entry in logs)
    assert any("阶段消息" in entry for entry in logs)


def test_task_manager_watchdog_fails_stalled_task():
    manager = TaskManager(max_workers=1, stall_timeout_seconds=0.5, watchdog_interval_seconds=0.2)
    release = threading.Event()

    def stalled(progress):
        progress(10, "开始长调用")
        assert not release.wait(5), "测试未触发看门狗就结束了"
        return {"never": True}

    task_id = manager.submit("generate", stalled)
    deadline = time.time() + 5
    while time.time() < deadline:
        task = manager.get(task_id)
        if task and task["status"] == "failed" and "停滞" in (task.get("error") or ""):
            break
        time.sleep(0.05)
    release.set()
    task = manager.get(task_id)
    assert task["status"] == "failed"
    assert "停滞" in task["error"]
    assert any("停滞" in entry for entry in task["logs"])


def test_task_manager_pause_resume_cancel_and_delete():
    manager = TaskManager(max_workers=1)
    reached = threading.Event()
    release = threading.Event()

    def pausable(progress):
        progress(10, "第一阶段")
        reached.set()
        assert release.wait(2)
        progress(60, "第二阶段")
        return {"done": True}

    task_id = manager.submit("generate", pausable)
    assert reached.wait(1)
    assert manager.pause(task_id)["status"] in {"pausing", "paused"}
    release.set()
    _wait_task_status(manager, task_id, {"paused"})
    manager.resume(task_id)
    _wait_task_status(manager, task_id, {"success"})
    assert manager.delete(task_id)["status"] == "success"
    assert manager.get(task_id) is None

    reached2 = threading.Event()
    release2 = threading.Event()

    def cancellable(progress):
        progress(10, "第一阶段")
        reached2.set()
        assert release2.wait(2)
        progress(50, "第二阶段")
        return {"should_not_finish": True}

    task_id = manager.submit("generate", cancellable)
    assert reached2.wait(1)
    assert manager.cancel(task_id)["status"] in {"cancelling", "cancelled"}
    release2.set()
    _wait_task_status(manager, task_id, {"cancelled"})
    manager.delete(task_id)


def test_token_usage_routes_are_admin_only():
    assert web_app.check_access('/api/token-usage', 'GET', 'admin') is True
    assert web_app.check_access('/api/token-usage', 'GET', 'creator') is False
    assert web_app.check_access('/api/token-usage/tasks/x', 'GET', 'viewer') is False


def test_generate_api_runs_background_task(monkeypatch):
    def fake_generate(request, config, progress, owner_user=None, **kwargs):
        progress(40, "mock writing")
        progress(90, "mock formatting")
        return {"article_id": "b" * 32, "title": request.topic.title}

    monkeypatch.setattr(web_app.article_store, "generate", fake_generate)
    client = TestClient(app)
    response = client.post(
        "/api/generate",
        json={
            "topic": {"title": "测试热点话题", "source": "manual", "score": 88},
            "options": {"with_images": False, "style": "deep"},
        },
    )
    assert response.status_code == 200
    task_id = response.json()["data"]["task_id"]
    task = None
    for _ in range(100):
        task = client.get(f"/api/tasks/{task_id}").json()["data"]
        if task["status"] in {"success", "failed"}:
            break
        time.sleep(0.01)
    assert task["status"] == "success"
    assert task["result"]["article_id"] == "b" * 32
    assert task["progress"] == 100


def test_hotspot_api_accepts_custom_keywords(monkeypatch):
    payload = {"items": [{"id": "1", "title": "AI大模型进展", "summary": "", "score": 80}], "updated_at": "now"}
    monkeypatch.setattr(web_app.hotspot_service, "cached", lambda: payload)
    response = TestClient(app).get("/api/hotspots?refresh=false&keywords=AI")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["query_keywords"] == ["AI"]
    assert data["items"][0]["matched_query_keywords"] == ["AI"]


def test_web_search_topics_api(monkeypatch):
    captured = {}

    def fake_search(self, query, **kwargs):
        captured.update(kwargs)
        return {"items": [{"title": query}], "time_range": kwargs["time_range"]}

    monkeypatch.setattr(web_app.TavilySearchService, "search", fake_search)
    response = TestClient(app).get("/api/search/topics?q=AI医疗&topic=news&time_range=30d&limit=5")
    assert response.status_code == 200
    assert response.json()["data"]["items"][0]["title"] == "AI医疗"
    assert captured["prefer_chinese"] is True
    response = TestClient(app).get("/api/search/topics?q=AI医疗&topic=news&time_range=30d&limit=5&prefer_chinese=false")
    assert captured["prefer_chinese"] is False


def test_task_control_api_and_delete_confirmation(monkeypatch):
    task_id = "9" * 32
    task = {"id": task_id, "kind": "generate", "status": "paused", "progress": 20, "logs": []}
    monkeypatch.setattr(web_app.task_manager, "get", lambda value: {**task, "id": value, "owner_user_id": "test-admin"})
    monkeypatch.setattr(web_app.task_manager, "pause", lambda value: {**task, "id": value})
    monkeypatch.setattr(web_app.task_manager, "delete", lambda value: {**task, "id": value, "status": "cancelled"})
    client = TestClient(app)
    assert client.post(f"/api/tasks/{task_id}/pause").status_code == 200
    assert client.delete(f"/api/tasks/{task_id}").status_code == 400
    response = client.delete(f"/api/tasks/{task_id}?confirm=true")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "cancelled"


def test_image_connection_endpoint_for_pillow(monkeypatch):
    monkeypatch.setattr(web_app.config_service, "load", lambda: Config())
    monkeypatch.setattr(
        web_app.ImageGenerator,
        "validate_connection",
        lambda self: {"provider": "pillow", "model": "local", "chargeable": False},
    )
    client = TestClient(app)
    response = client.post("/api/config/test/image")
    assert response.status_code == 200
    assert response.json()["data"]["provider"] == "pillow"


def test_delete_article_requires_confirmation(monkeypatch):
    article_id = "d" * 32
    monkeypatch.setattr(web_app.article_store, "metadata", lambda value: {"id": value, "owner_user_id": "test-admin"})
    monkeypatch.setattr(
        web_app.article_store,
        "delete",
        lambda value: {"article_id": value, "title": "测试", "remote_draft_preserved": False},
    )
    client = TestClient(app)
    assert client.delete(f"/api/articles/{article_id}").status_code == 400
    response = client.delete(f"/api/articles/{article_id}?confirm=true")
    assert response.status_code == 200
    assert response.json()["data"]["article_id"] == article_id


def test_push_requires_human_compliance_confirmation():
    client = TestClient(app)
    response = client.post(
        f"/api/articles/{'c' * 32}/push",
        json={"confirm_reviewed": False, "confirm_ai_disclosure": False},
    )
    assert response.status_code == 400
    assert "人工审核" in response.json()["detail"]
