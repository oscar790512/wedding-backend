from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from seat_map_layout import TABLES


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "wedding_seats.jpg"
OUTPUT = Path(__file__).resolve().parents[1] / "app/static/seat-videos/table-map-system-labels.png"
FONT_PATH = Path("/System/Library/Fonts/STHeiti Medium.ttc")
LABEL_FILL = (93, 53, 22)
NUMBER_FILL = (150, 99, 43)
LABEL_STROKE = (244, 242, 234)
LABEL_STROKE_WIDTH = 1


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_PATH), size=size)


def wrap_text(draw: ImageDraw.ImageDraw, text: str, selected_font: ImageFont.FreeTypeFont) -> list[str]:
    max_width = 150
    lines: list[str] = []
    current = ""
    for character in text:
        candidate = current + character
        bbox = draw.textbbox((0, 0), candidate, font=selected_font)
        if current and bbox[2] - bbox[0] > max_width:
            lines.append(current)
            current = character
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def draw_centered_label(draw: ImageDraw.ImageDraw, table: dict) -> None:
    name_font = font(29)
    number_font = font(23)
    name_lines = table.get("label_lines") or wrap_text(draw, table["table_name"], name_font)
    lines = [
        (f"第 {table['table_number']} 桌", number_font, NUMBER_FILL),
        *((line, name_font, LABEL_FILL) for line in name_lines),
    ]
    line_gap = 0
    metrics = []
    total_height = 0

    for line, selected_font, fill in lines:
        bbox = draw.textbbox((0, 0), line, font=selected_font, stroke_width=LABEL_STROKE_WIDTH)
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        metrics.append((line, selected_font, fill, width, height, bbox))
        total_height += height

    total_height += line_gap * (len(metrics) - 1)
    y = table["y"] - total_height / 2

    for line, selected_font, fill, width, height, bbox in metrics:
        x = table["x"] - width / 2 - bbox[0]
        line_y = y - bbox[1]
        draw.text(
            (x, line_y),
            line,
            font=selected_font,
            fill=fill,
            stroke_width=LABEL_STROKE_WIDTH,
            stroke_fill=LABEL_STROKE,
        )
        y += height + line_gap


def main() -> None:
    image = Image.open(SOURCE).convert("RGB")
    draw = ImageDraw.Draw(image)
    for table in TABLES:
        draw_centered_label(draw, table)
    image.save(OUTPUT)


if __name__ == "__main__":
    main()
