"""Markdown → 公众号 HTML 排版模块。

把 Markdown 正文转换为公众号可用的 HTML（内联样式，兼容微信编辑器）。
支持：标题、段落、粗体/斜体、行内代码、代码块、列表、引用、图片、链接（转脚注）。
"""

from __future__ import annotations

import html
import logging
import re
from typing import List

logger = logging.getLogger(__name__)

# 段落样式（微信编辑器对部分 CSS 支持有限，用常见安全属性）
_P_STYLE = (
    "margin: 0 0 16px 0; padding: 0; line-height: 1.9; "
    "font-size: 15px; color: #3f3f3f; letter-spacing: 0.5px; text-align: left;"
)
_H1_STYLE = "font-size: 20px; font-weight: bold; color: #1a1a1a; margin: 24px 0 12px 0; text-align: left;"
_H2_STYLE = "font-size: 18px; font-weight: bold; color: #1a1a1a; margin: 22px 0 10px 0; text-align: left;"
_H3_STYLE = "font-size: 16px; font-weight: bold; color: #2b2b2b; margin: 18px 0 8px 0; text-align: left;"
_CODE_STYLE = (
    "background-color: #f5f5f5; border-radius: 3px; padding: 2px 4px; "
    "font-family: Consolas, monospace; font-size: 13px; color: #c7254e;"
)
_BLOCKQUOTE_STYLE = (
    "margin: 12px 0; padding: 10px 16px; border-left: 4px solid #4a90d9; "
    "background-color: #f7f9fc; color: #555; text-align: left;"
)
_LI_STYLE = "margin: 4px 0; line-height: 1.8; font-size: 15px; color: #3f3f3f; text-align: left;"
_IMG_STYLE = "max-width: 100%; border-radius: 6px; margin: 12px 0;"


def normalize_markdown(md: str) -> str:
    """清理模型常见的非标准 Markdown 输出。

    处理整段代码围栏、全角井号、标题井号后缺空格以及孤立的 ``#``，
    避免这些标记以普通文字出现在公众号预览中。
    """
    text = (md or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    fenced = re.fullmatch(
        r"\s*```(?:markdown|md)?\s*\n?([\s\S]*?)\n?```\s*", text, flags=re.I
    )
    if fenced:
        text = fenced.group(1)
    normalized: List[str] = []
    for raw in text.split("\n"):
        line = raw.replace("＃", "#").rstrip()
        if re.fullmatch(r"\s*#{1,6}\s*", line):
            continue
        heading = re.match(r"^(\s*)(#{1,6})(?!#)\s*(\S.*)$", line)
        if heading:
            line = f"{heading.group(1)}{heading.group(2)} {heading.group(3).strip()}"
        normalized.append(line)
    return "\n".join(normalized).strip()


class MarkdownFormatter:
    """极简 Markdown → 微信 HTML 转换器（覆盖公众号文章常用语法）。"""

    def __init__(self):
        self._footnotes: List[str] = []

    # ------------------------------------------------------------------
    def convert(self, md: str, base_url: str = "") -> str:
        """转换全文。base_url 用于把相对图片路径补全（本地图床场景）。"""
        self._footnotes = []
        lines = normalize_markdown(md).split("\n")
        html_parts: List[str] = []
        i = 0
        in_code_block = False
        code_buf: List[str] = []

        while i < len(lines):
            line = lines[i].rstrip()

            # 代码块
            if line.strip().startswith("```"):
                if not in_code_block:
                    in_code_block = True
                    code_buf = []
                else:
                    in_code_block = False
                    html_parts.append(self._render_code_block(code_buf))
                i += 1
                continue
            if in_code_block:
                code_buf.append(line)
                i += 1
                continue

            # 空行
            if not line.strip():
                i += 1
                continue

            # 标题
            m = re.match(r"^(#{1,6})\s+(.*)$", line)
            if m:
                source_level = len(m.group(1))
                level = min(source_level, 3)
                text = self._inline(m.group(2), base_url)
                style = {1: _H1_STYLE, 2: _H2_STYLE, 3: _H3_STYLE}[level]
                html_parts.append(f"<h{level} style=\"{style}\">{text}</h{level}>")
                i += 1
                continue

            # 引用
            if line.startswith(">"):
                buf = []
                while i < len(lines) and lines[i].strip().startswith(">"):
                    buf.append(lines[i].strip().lstrip(">").strip())
                    i += 1
                quote = "<br>".join(self._inline(x, base_url) for x in buf)
                html_parts.append(f"<blockquote style=\"{_BLOCKQUOTE_STYLE}\">{quote}</blockquote>")
                continue

            # 列表
            m = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
            if m:
                items = []
                while i < len(lines):
                    mm = re.match(r"^(\s*)[-*+]\s+(.*)$", lines[i].rstrip())
                    if not mm:
                        break
                    items.append(self._inline(mm.group(2), base_url))
                    i += 1
                lis = "".join(
                    f"<li style=\"{_LI_STYLE}\">{it}</li>" for it in items
                )
                html_parts.append(f"<ul style=\"padding-left: 20px;\">{lis}</ul>")
                continue

            m = re.match(r"^(\s*)\d+[.、]\s*(.*)$", line)
            if m:
                items = []
                while i < len(lines):
                    mm = re.match(r"^(\s*)\d+[.、]\s*(.*)$", lines[i].rstrip())
                    if not mm:
                        break
                    items.append(self._inline(mm.group(2), base_url))
                    i += 1
                lis = "".join(
                    f"<li style=\"{_LI_STYLE}\">{it}</li>" for it in items
                )
                html_parts.append(f"<ol style=\"padding-left: 20px;\">{lis}</ol>")
                continue

            # 分割线
            if re.match(r"^\s*([-*_])\1{2,}\s*$", line):
                html_parts.append("<hr style=\"border: none; border-top: 1px solid #eee; margin: 24px 0;\">")
                i += 1
                continue

            # 图片（独占一行）
            m = re.match(r"^!\[(.*?)\]\((.*?)\)\s*$", line)
            if m:
                alt, src = m.group(1), m.group(2)
                if base_url and not src.startswith(("http://", "https://", "data:")):
                    src = base_url.rstrip("/") + "/" + src.lstrip("/")
                html_parts.append(
                    f"<img src=\"{src}\" alt=\"{alt}\" style=\"{_IMG_STYLE}\"/>"
                )
                i += 1
                continue

            # 普通段落（合并连续行）
            buf = [line]
            i += 1
            while (
                i < len(lines)
                and lines[i].strip()
                and not lines[i].strip().startswith(("#", ">", "-", "*", "```", "!"))
                and not re.match(r"^\s*\d+[.、]", lines[i])
            ):
                buf.append(lines[i].rstrip())
                i += 1
            para = "".join(buf)
            html_parts.append(f"<p style=\"{_P_STYLE}\">{self._inline(para, base_url)}</p>")

        # 脚注（外链转脚注）
        foot = ""
        if self._footnotes:
            notes = "<br>".join(
                f"[{i + 1}] {html.escape(u)}" for i, u in enumerate(self._footnotes)
            )
            foot = (
                f"<p style=\"{_P_STYLE}color:#999; font-size:12px;\">"
                f"<strong>参考资料</strong><br>{notes}</p>"
            )

        if in_code_block and code_buf:
            # 未闭合代码围栏：安全落地缓冲内容，避免尾部正文被静默丢弃
            html_parts.append(self._render_code_block(code_buf))

        return "\n".join(html_parts) + foot

    # ------------------------------------------------------------------
    def _inline(self, text: str, base_url: str = "") -> str:
        """行内语法：先转义用户文本，再生成受控 HTML 标签。"""
        text = html.escape(text, quote=False)

        # 图片
        def _image(m: re.Match) -> str:
            alt, src = m.group(1), m.group(2)
            if base_url and not src.startswith(("http://", "https://", "data:")):
                src = base_url.rstrip("/") + "/" + src.lstrip("/")
            return (
                f"<img src=\"{html.escape(src, quote=True)}\" "
                f"alt=\"{html.escape(alt, quote=True)}\" style=\"{_IMG_STYLE}\"/>"
            )

        text = re.sub(r"!\[(.*?)\]\((.*?)\)", _image, text)
        # 行内代码
        text = re.sub(
            r"`([^`]+)`",
            lambda m: f"<code style=\"{_CODE_STYLE}\">{m.group(1)}</code>",
            text,
        )
        # 粗体/斜体
        text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)

        # 链接 → 脚注
        def _link(m: re.Match) -> str:
            label, url = m.group(1), m.group(2)
            if url.startswith(("http://", "https://")):
                self._footnotes.append(url)
                idx = len(self._footnotes)
                return f"{label}<sup style=\"color:#4a90d9;\">[{idx}]</sup>"
            return label

        return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _link, text)
    def _render_code_block(self, lines: List[str]) -> str:
        code = "\n".join(lines).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return (
            "<pre style=\"background-color:#f8f8f8; border:1px solid #e5e5e5; "
            "border-radius:4px; padding:12px; overflow-x:auto; font-size:13px;"
            "font-family:Consolas,monospace; line-height:1.6;\">"
            f"{code}</pre>"
        )



