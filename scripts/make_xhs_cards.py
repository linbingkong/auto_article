from pathlib import Path
import random

from PIL import Image, ImageDraw, ImageFont

OUT = Path(r"F:\harness_enginnering\auto_article\docs\assets\xiaohongshu")
OUT.mkdir(parents=True, exist_ok=True)
W, H = 1080, 1440

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
]


def font(size):
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def gradient():
    img = Image.new("RGB", (W, H))
    top = (242, 249, 245)
    bottom = (251, 247, 236)
    for y in range(H):
        t = y / (H - 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        ImageDraw.Draw(img).rectangle([(0, y), (W, y + 1)], fill=color)
    return img


def decorate(draw, seed):
    rng = random.Random(seed)
    for _ in range(6):
        x, y = rng.randint(60, W - 60), rng.randint(60, H - 60)
        r = rng.randint(60, 170)
        draw.ellipse([(x - r, y - r), (x + r, y + r)], outline=(38, 123, 84, 28), width=2)


def center(draw, y, text, f, fill, letter=None):
    box = draw.textbbox((0, 0), text, font=f)
    draw.text(((W - box[2] + box[0]) / 2, y), text, font=f, fill=fill)
    return box


def chip(draw, cx, y, text, f, fill=(31, 107, 71), bg=(255, 255, 255)):
    box = draw.textbbox((0, 0), text, font=f)
    tw, th = box[2] - box[0], box[3] - box[1]
    pad_x, pad_y = 26, 14
    draw.rounded_rectangle(
        [(cx - tw / 2 - pad_x, y), (cx + tw / 2 + pad_x, y + th + pad_y * 2)],
        radius=999, fill=bg, outline=(31, 107, 71), width=2,
    )
    draw.text((cx - tw / 2 - box[0], y + pad_y - box[1]), text, font=f, fill=fill)


def card_01():
    img = gradient()
    draw = ImageDraw.Draw(img, "RGBA")
    decorate(draw, 7)
    f_eye = font(34)
    f_title = font(112)
    f_sub = font(40)
    f_small = font(30)
    chip(draw, W / 2, 250, "公众号内容智能体 · 观思辩明", f_eye)
    center(draw, 430, "从热点到草稿", f_title, (23, 53, 42))
    center(draw, 580, "一站式完成", f_title, (23, 53, 42))
    center(draw, 800, "选题 · 搜索 · 写作 · 配图 · 草稿管理", f_sub, (76, 110, 92))
    draw.rounded_rectangle([(140, 940), (W - 140, 1120)], radius=26, fill=(240, 248, 243), outline=(38, 123, 84), width=2)
    center(draw, 985, "榜单热点 / 榜单外搜索", f_small, (31, 107, 71))
    center(draw, 1055, "资料研究 / 文章初稿 / 封面配图", f_small, (31, 107, 71))
    center(draw, 1290, "把时间留给观点和表达", f_small, (154, 116, 40))
    img.save(OUT / "01-cover.png")


def card_09():
    img = gradient()
    draw = ImageDraw.Draw(img, "RGBA")
    decorate(draw, 21)
    f_title = font(88)
    f_sub = font(42)
    f_cta = font(46)
    f_brand = font(38)
    center(draw, 360, "从热点到公众号草稿", f_title, (23, 53, 42))
    center(draw, 500, "把时间留给", f_sub, (76, 110, 92))
    center(draw, 575, "观点和表达", f_sub, (76, 110, 92))
    draw.rounded_rectangle([(170, 760), (W - 170, 950)], radius=34, fill=(31, 107, 71))
    center(draw, 810, "评论 / 私信：工作流", f_cta, (255, 255, 255))
    center(draw, 1060, "附完整使用教程与体验方式", f_sub, (76, 110, 92))
    chip(draw, W / 2, 1230, "观思辩明", f_brand)
    img.save(OUT / "10-action.png")


card_01()
card_09()
for name in ("01-cover.png", "10-action.png"):
    im = Image.open(OUT / name)
    print(name, im.size)
