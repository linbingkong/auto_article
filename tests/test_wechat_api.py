import pytest

from wechat_agent.config import Config
from wechat_agent.wechat_api import WechatAPIError, WechatClient, build_draft_payload


def test_build_draft_payload_truncates_digest():
    payload = build_draft_payload(
        title="测试标题",
        digest="摘" * 200,
        content_html="<p>正文</p>",
        thumb_media_id="media-1",
    )
    assert payload["title"] == "测试标题"
    assert len(payload["digest"]) == 120
    assert payload["thumb_media_id"] == "media-1"
    assert payload["need_open_comment"] == 1


def test_invalid_token_clears_cache():
    client = WechatClient(Config())
    client._token = "stale-token"
    client._token_expire = 9999999999
    with pytest.raises(WechatAPIError):
        client._check({"errcode": 40001, "errmsg": "invalid credential"})
    assert client._token == ""
    assert client._token_expire == 0.0
