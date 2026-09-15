from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "wedding_seats.jpg"
OUTPUT = Path(__file__).resolve().parents[1] / "app/static/seat-videos/table-map-system-labels.png"
FONT_PATH = Path("/System/Library/Fonts/STHeiti Medium.ttc")
LABEL_FILL = (93, 53, 22)
LABEL_STROKE = (244, 242, 234)
LABEL_STROKE_WIDTH = 1


TABLE_LABELS = [
    {"x": 543, "y": 404, "lines": ["主桌"], "size": 30},
    {"x": 232, "y": 379, "lines": ["凍齡", "男神", "女神"], "size": 21},
    {"x": 850, "y": 379, "lines": ["男方", "家人"], "size": 22},
    {"x": 232, "y": 525, "lines": ["六腳鄉", "黃氏親", "家"], "size": 21},
    {"x": 418, "y": 548, "lines": ["看著", "我長", "大"], "size": 21},
    {"x": 669, "y": 548, "lines": ["男方", "家人3"], "size": 22},
    {"x": 850, "y": 525, "lines": ["男方", "家人2"], "size": 22},
    {"x": 232, "y": 672, "lines": ["沒有血", "緣關係", "的姊妹"], "size": 19},
    {"x": 418, "y": 674, "lines": ["我愛萬", "金萬金", "愛我"], "size": 21},
    {"x": 669, "y": 674, "lines": ["男方", "家人5"], "size": 22},
    {"x": 850, "y": 671, "lines": ["男方", "家人4"], "size": 22},
    {"x": 232, "y": 818, "lines": ["台中", "SGS好", "夥伴"], "size": 21},
    {"x": 418, "y": 798, "lines": ["楠梓國", "中寶貝", "們"], "size": 21},
    {"x": 669, "y": 798, "lines": ["男方", "同事"], "size": 22},
    {"x": 850, "y": 818, "lines": ["男方長輩", "好友"], "size": 22},
    {"x": 232, "y": 964, "lines": ["SGS", "好夥", "伴"], "size": 21},
    {"x": 418, "y": 928, "lines": ["一輩子", "的楠中", "306"], "size": 21},
    {"x": 669, "y": 924, "lines": ["男方", "同事2"], "size": 22},
    {"x": 850, "y": 964, "lines": ["男方", "好友1"], "size": 22},
    {"x": 232, "y": 1110, "lines": ["男方研究所", "同學"], "size": 19},
    {"x": 418, "y": 1048, "lines": ["屏科生", "科不顆", "顆"], "size": 21},
    {"x": 669, "y": 1053, "lines": ["男方", "同事3"], "size": 22},
    {"x": 850, "y": 1110, "lines": ["男方", "好友2"], "size": 22},
    {"x": 232, "y": 1256, "lines": ["預備桌"], "size": 26},
    {"x": 418, "y": 1176, "lines": ["當不成同", "事當永遠", "好麻吉"], "size": 19},
    {"x": 670, "y": 1175, "lines": ["男方", "同事6"], "size": 22},
    {"x": 850, "y": 1256, "lines": ["男方", "同事4"], "size": 22},
    {"x": 418, "y": 1321, "lines": ["男方", "同事5"], "size": 22},
]


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_PATH), size=size)


def draw_centered_label(draw: ImageDraw.ImageDraw, label: dict) -> None:
    selected_font = font(label["size"])
    line_gap = -2 if len(label["lines"]) >= 3 else 2
    metrics = []
    total_height = 0

    for line in label["lines"]:
        bbox = draw.textbbox((0, 0), line, font=selected_font, stroke_width=LABEL_STROKE_WIDTH)
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        metrics.append((line, width, height, bbox))
        total_height += height

    total_height += line_gap * (len(metrics) - 1)
    y = label["y"] - total_height / 2

    for line, width, height, bbox in metrics:
        x = label["x"] - width / 2 - bbox[0]
        line_y = y - bbox[1]
        draw.text(
            (x, line_y),
            line,
            font=selected_font,
            fill=LABEL_FILL,
            stroke_width=LABEL_STROKE_WIDTH,
            stroke_fill=LABEL_STROKE,
        )
        y += height + line_gap


def main() -> None:
    image = Image.open(SOURCE).convert("RGB")
    draw = ImageDraw.Draw(image)
    for label in TABLE_LABELS:
        draw_centered_label(draw, label)
    image.save(OUTPUT)


if __name__ == "__main__":
    main()
