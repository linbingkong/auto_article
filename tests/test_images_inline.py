import shutil
import sys
from pathlib import Path
sys.path.insert(0, r'F:\harness_enginnering\auto_article\src')
from wechat_agent.config import Config, ImageConfig
from wechat_agent.images import ImageGenerator

ROOT = Path(r'F:\harness_enginnering\auto_article\output\_inline_test')
shutil.rmtree(ROOT, ignore_errors=True)


def test_cjk_font_detection_rejects_repeated_missing_glyph():
    class Mask:
        size = (20, 30)

        def __init__(self, value):
            self.value = value

        def __bytes__(self):
            return self.value.encode("utf-8")

    class MissingGlyphFont:
        def getmask(self, _char):
            return Mask("same-box")

    class ChineseFont:
        def getmask(self, char):
            return Mask(char)

    assert ImageGenerator._font_has_cjk(MissingGlyphFont()) is False
    assert ImageGenerator._font_has_cjk(ChineseFont()) is True


def test_inline_api_generates_real_illustration_and_falls_back(monkeypatch):
    calls = []

    class Response:
        status_code = 200
        reason = 'OK'
        text = ''
        content = b'PNGDATA'

        def json(self):
            return {'output': {'choices': [{'message': {'content': [{'image': 'https://example.test/img.png'}]}}]}}

    def fake_post(method, url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr('requests.request', fake_post)
    cfg = Config()
    cfg.image = ImageConfig(provider='dashscope', api_key='test', model='qwen-image-test', inline_images=2, base_url='https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation')
    paths = ImageGenerator(cfg).generate_inline(['## 一、关键事实\n\n第一段内容', '## 二、影响分析\n\n第二段内容'], ROOT)
    assert len(paths) == 2
    assert (ROOT / 'inline_1.png').read_bytes() == b'PNGDATA'
    assert calls[0][0] == 'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation'
    prompt = calls[0][1]['json']['input']['messages'][0]['content'][0]['text']
    assert '文章的新闻场景插画' in prompt
    assert '绝对不能出现任何文字' in prompt
    assert calls[0][1]['json']['parameters']['size'] == '1280*720'


def test_inline_api_failure_skips_text_card(monkeypatch):
    (ROOT / 'inline_1.png').unlink(missing_ok=True)

    def fake_post(method, url, **kwargs):
        raise RuntimeError('network down')

    monkeypatch.setattr('requests.request', fake_post)
    cfg = Config()
    cfg.image = ImageConfig(provider='dashscope', api_key='test', model='qwen-image-test', inline_images=1, base_url='https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation')
    paths = ImageGenerator(cfg).generate_inline(['## 章节\n\n要点内容'], ROOT)
    assert paths == []
    assert not (ROOT / 'inline_1.png').exists()
