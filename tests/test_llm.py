import pytest

from wechat_agent.config import LLMConfig
from wechat_agent.llm import LLMClient, LLMError


class _Response:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {
                        "content": None,
                        "reasoning_content": "我们需要分析原文结构并开始编辑。",
                    },
                }
            ]
        }


def test_reasoning_content_is_never_returned_as_final_answer(monkeypatch):
    client = LLMClient(LLMConfig(base_url="http://model.test/v1", model="reasoner"))
    monkeypatch.setattr(client._session, "post", lambda *args, **kwargs: _Response())
    with pytest.raises(LLMError, match="拒绝把内部推理当作正文"):
        client.chat([{"role": "user", "content": "润色文章"}])
