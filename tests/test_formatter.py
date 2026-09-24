import json
import shutil
from pathlib import Path

from wechat_agent.article_store import ArticleStore
from wechat_agent.formatter import MarkdownFormatter


def test_markdown_formatter_basic():
    md = """## 标题

这是**粗体**和`代码`。

- 第一项
- 第二项

> 引用内容

![配图](inline_1.png)
"""
    html = MarkdownFormatter().convert(md)
    assert "<h2" in html
    assert "<strong>粗体</strong>" in html
    assert "inline_1.png" in html
    assert "<li" in html
    assert "<blockquote" in html


def test_wechat_text_blocks_use_standard_left_alignment():
    html = MarkdownFormatter().convert("# 一级\n\n## 二级\n\n正文段落。\n\n- 列表项\n\n> 引用内容")
    assert html.count("text-align: left;") >= 5
    assert "text-align: start" not in html
    assert "text-align: justify" not in html
    for tag in ("<h1", "<h2", "<p", "<li", "<blockquote"):
        fragment = html[html.index(tag):]
        assert "text-align: left;" in fragment.split(">", 1)[0]


def test_opening_old_article_rebuilds_html_with_safe_alignment():
    root = Path("output/_article_alignment_test")
    article_id = "a" * 32
    article_dir = root / article_id
    shutil.rmtree(root, ignore_errors=True)
    article_dir.mkdir(parents=True)
    (article_dir / "metadata.json").write_text(json.dumps({"id": article_id, "title": "测试文章", "title_candidates": ["测试文章"], "assets": []}), encoding="utf-8")
    (article_dir / "article.md").write_text("## 标题\n\n这是正文段落。", encoding="utf-8")
    (article_dir / "article.html").write_text('<p style="text-align:start">旧正文</p>', encoding="utf-8")
    store = ArticleStore.__new__(ArticleStore)
    store.root = root
    store.history = None
    try:
        data = store.get(article_id)
        assert "text-align: left;" in data["content_html"]
        assert "text-align:start" not in (article_dir / "article.html").read_text(encoding="utf-8")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_rich_copy_normalizes_nonstandard_alignment():
    source = Path("web/articles.js").read_text(encoding="utf-8")
    assert "allowedAlign=new Set(['left','center','right'])" in source
    assert "el.style.removeProperty('text-align')" in source
    assert "el.style.setProperty('text-align','left')" in source


def test_links_become_footnotes():
    html = MarkdownFormatter().convert("阅读[官方文档](https://example.com/doc)。")
    assert "参考资料" in html
    assert "https://example.com/doc" in html


def test_nonstandard_headings_are_normalized():
    html = MarkdownFormatter().convert("```markdown\n##没有空格\n\n＃＃＃全角井号\n\n#\n正文\n```")
    assert "没有空格" in html and "<h2" in html
    assert "全角井号" in html and "<h3" in html
    assert ">#<" not in html
