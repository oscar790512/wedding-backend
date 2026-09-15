from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import tempfile
from pathlib import Path


WIDTH = 540
HEIGHT = 960
FPS = 15
SECONDS = 4
FRAME_COUNT = FPS * SECONDS
BACKGROUND = (244, 242, 234)
HIGHLIGHT = (255, 214, 94)
HIGHLIGHT_DARK = (160, 105, 48)


# Coordinates are measured against /Users/oscar/wedding-system/wedding_seats.jpg
# at 1076 x 1522. The uploaded floor plan has 27 regular tables plus the main table.
TABLE_POSITIONS = [
    {"number": 1, "x": 232, "y": 379, "radius": 58, "table_name": "凍齡男神女神"},
    {"number": 2, "x": 850, "y": 379, "radius": 58, "table_name": "男方家人"},
    {"number": 3, "x": 232, "y": 525, "radius": 58, "table_name": "六腳鄉黃氏親家"},
    {"number": 4, "x": 418, "y": 548, "radius": 58, "table_name": "看著我長大"},
    {"number": 5, "x": 669, "y": 548, "radius": 58, "table_name": "男方家人3"},
    {"number": 6, "x": 850, "y": 525, "radius": 58, "table_name": "男方家人2"},
    {"number": 7, "x": 232, "y": 672, "radius": 58, "table_name": "沒有血緣關係的姊妹"},
    {"number": 8, "x": 418, "y": 674, "radius": 58, "table_name": "我愛萬金萬金愛我"},
    {"number": 9, "x": 669, "y": 674, "radius": 58, "table_name": "男方家人5"},
    {"number": 10, "x": 850, "y": 671, "radius": 58, "table_name": "男方家人4"},
    {"number": 11, "x": 232, "y": 818, "radius": 58, "table_name": "台中SGS好夥伴"},
    {"number": 12, "x": 418, "y": 798, "radius": 58, "table_name": "楠梓國中寶貝們"},
    {"number": 13, "x": 669, "y": 798, "radius": 58, "table_name": "男方同事"},
    {"number": 14, "x": 850, "y": 818, "radius": 58, "table_name": "男方長輩好友"},
    {"number": 15, "x": 232, "y": 964, "radius": 58, "table_name": "SGS好夥伴"},
    {"number": 16, "x": 418, "y": 928, "radius": 58, "table_name": "一輩子的楠中306"},
    {"number": 17, "x": 669, "y": 924, "radius": 58, "table_name": "男方同事2"},
    {"number": 18, "x": 850, "y": 964, "radius": 58, "table_name": "男方好友1"},
    {"number": 19, "x": 232, "y": 1110, "radius": 58, "table_name": "男方研究所同學"},
    {"number": 20, "x": 418, "y": 1048, "radius": 58, "table_name": "屏科生科不顆顆"},
    {"number": 21, "x": 669, "y": 1053, "radius": 58, "table_name": "男方同事3"},
    {"number": 22, "x": 850, "y": 1110, "radius": 58, "table_name": "男方好友2"},
    {"number": 23, "x": 232, "y": 1256, "radius": 58, "table_name": "預備桌"},
    {"number": 24, "x": 418, "y": 1176, "radius": 58, "table_name": "當不成同事當永遠好麻吉"},
    {"number": 25, "x": 670, "y": 1175, "radius": 58, "table_name": "男方同事6"},
    {"number": 26, "x": 850, "y": 1256, "radius": 58, "table_name": "男方同事4"},
    {"number": 27, "x": 418, "y": 1321, "radius": 58, "table_name": "男方同事5"},
]
MAIN_TABLE = {"number": 28, "x": 543, "y": 404, "radius": 74, "main": True}


def ease(value: float) -> float:
    clipped = min(max(value, 0), 1)
    return 0.5 - 0.5 * math.cos(math.pi * clipped)


def parse_ppm(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    if data[:2] != b"P6":
        raise ValueError(f"{path} is not a binary PPM image")

    index = 2
    parts = []
    while len(parts) < 3:
        while data[index] in b" \n\r\t":
            index += 1
        if data[index] == ord("#"):
            while data[index] not in b"\n\r":
                index += 1
            continue
        start = index
        while data[index] not in b" \n\r\t":
            index += 1
        parts.append(int(data[start:index]))

    while data[index] in b" \n\r\t":
        index += 1
    width, height, max_value = parts
    if max_value != 255:
        raise ValueError("Only 8-bit PPM images are supported")
    return width, height, data[index:]


def convert_source_to_ppm(source: Path, destination: Path) -> tuple[int, int, bytes]:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(source),
            str(destination),
        ],
        check=True,
    )
    return parse_ppm(destination)


def lerp(start: float, end: float, t: float) -> float:
    return start + (end - start) * t


def frame_transform(source_width: int, source_height: int, target: dict, frame_index: int) -> tuple[float, float, float]:
    intro_scale = min(WIDTH / source_width, HEIGHT / source_height)
    zoom_t = ease((frame_index - 8) / 24)
    target_scale = 1.25 if target.get("main") else 1.35
    scale = lerp(intro_scale, target_scale, zoom_t)
    center_x = lerp(source_width / 2, target["x"], zoom_t)
    center_y = lerp(source_height / 2, target["y"], zoom_t)
    return scale, center_x, center_y


def sample_source(
    source_pixels: bytes,
    source_width: int,
    source_height: int,
    source_x: float,
    source_y: float,
) -> tuple[int, int, int]:
    x = round(source_x)
    y = round(source_y)
    if x < 0 or y < 0 or x >= source_width or y >= source_height:
        return BACKGROUND
    index = (y * source_width + x) * 3
    return source_pixels[index], source_pixels[index + 1], source_pixels[index + 2]


def blend_pixel(
    image: bytearray,
    x: int,
    y: int,
    color: tuple[int, int, int],
    alpha: float,
) -> None:
    if x < 0 or y < 0 or x >= WIDTH or y >= HEIGHT:
        return
    index = (y * WIDTH + x) * 3
    image[index] = round(image[index] * (1 - alpha) + color[0] * alpha)
    image[index + 1] = round(image[index + 1] * (1 - alpha) + color[1] * alpha)
    image[index + 2] = round(image[index + 2] * (1 - alpha) + color[2] * alpha)


def draw_ring(
    image: bytearray,
    cx: int,
    cy: int,
    radius: int,
    thickness: int,
    color: tuple[int, int, int],
    alpha: float,
) -> None:
    outer = radius + thickness
    inner = max(radius - thickness, 0)
    outer2 = outer * outer
    inner2 = inner * inner
    for y in range(cy - outer, cy + outer + 1):
        for x in range(cx - outer, cx + outer + 1):
            distance2 = (x - cx) ** 2 + (y - cy) ** 2
            if inner2 <= distance2 <= outer2:
                blend_pixel(image, x, y, color, alpha)


def draw_highlight(
    image: bytearray,
    source_width: int,
    source_height: int,
    target: dict,
    frame_index: int,
) -> None:
    scale, center_x, center_y = frame_transform(source_width, source_height, target, frame_index)
    target_x = round((target["x"] - center_x) * scale + WIDTH / 2)
    target_y = round((target["y"] - center_y) * scale + HEIGHT / 2)
    radius = max(round(target["radius"] * scale), 24)

    if frame_index < 30:
        alpha = 0.35 + 0.45 * ease(frame_index / 30)
    else:
        blink_index = min((frame_index - 30) // 3, 9)
        alpha = 0.82 if blink_index % 2 == 0 else 0.2

    draw_ring(image, target_x, target_y, radius + 12, 8, HIGHLIGHT, alpha)
    draw_ring(image, target_x, target_y, radius + 2, 4, HIGHLIGHT_DARK, alpha)


def render_frame(
    source_pixels: bytes,
    source_width: int,
    source_height: int,
    target: dict,
    frame_index: int,
) -> bytearray:
    scale, center_x, center_y = frame_transform(source_width, source_height, target, frame_index)
    image = bytearray(WIDTH * HEIGHT * 3)

    for y in range(HEIGHT):
        source_y = (y - HEIGHT / 2) / scale + center_y
        for x in range(WIDTH):
            source_x = (x - WIDTH / 2) / scale + center_x
            color = sample_source(source_pixels, source_width, source_height, source_x, source_y)
            index = (y * WIDTH + x) * 3
            image[index : index + 3] = bytes(color)

    draw_highlight(image, source_width, source_height, target, frame_index)
    return image


def write_ppm(path: Path, image: bytearray) -> None:
    path.write_bytes(f"P6\n{WIDTH} {HEIGHT}\n255\n".encode("ascii") + bytes(image))


def encode_video(frame_dir: Path, video_path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-framerate",
            str(FPS),
            "-i",
            str(frame_dir / "frame-%03d.ppm"),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(video_path),
        ],
        check=True,
    )


def encode_preview(source_frame: Path, preview_path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(source_frame),
            "-frames:v",
            "1",
            "-c:v",
            "png",
            str(preview_path),
        ],
        check=True,
    )


def render_asset(
    source_pixels: bytes,
    source_width: int,
    source_height: int,
    target: dict,
    output_dir: Path,
    filename_stem: str,
    table_name: str,
) -> dict:
    video_path = output_dir / f"{filename_stem}.mp4"
    preview_path = output_dir / f"{filename_stem}.png"

    with tempfile.TemporaryDirectory(prefix=f"seat-video-{filename_stem}-") as temp_name:
        frame_dir = Path(temp_name)
        for frame_index in range(FRAME_COUNT):
            write_ppm(
                frame_dir / f"frame-{frame_index:03d}.ppm",
                render_frame(source_pixels, source_width, source_height, target, frame_index),
            )
        encode_video(frame_dir, video_path)
        encode_preview(frame_dir / "frame-034.ppm", preview_path)

    return {
        "seat_video_key": filename_stem,
        "table_name": table_name,
        "video_filename": video_path.name,
        "preview_filename": preview_path.name,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate LINE seat lookup videos from the real floor plan image.")
    parser.add_argument(
        "--source",
        default="../wedding_seats.jpg",
        help="source floor plan image",
    )
    parser.add_argument(
        "--output-dir",
        default="app/static/seat-videos",
        help="directory for generated videos and previews",
    )
    parser.add_argument(
        "--only",
        default="",
        help="render one asset only, such as table-03 or main-table",
    )
    args = parser.parse_args()

    source = Path(args.source)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="seat-source-") as temp_name:
        source_width, source_height, source_pixels = convert_source_to_ppm(
            source,
            Path(temp_name) / "source.ppm",
        )

    requested = args.only.strip()
    manifest = []
    for table in TABLE_POSITIONS:
        stem = f"table-{table['number']:02d}"
        if requested and requested != stem:
            continue
        manifest.append(
            render_asset(
                source_pixels,
                source_width,
                source_height,
                table,
                output_dir,
                stem,
                table["table_name"],
            )
        )

    if not requested or requested == "main-table":
        manifest.append(
            render_asset(
                source_pixels,
                source_width,
                source_height,
                MAIN_TABLE,
                output_dir,
                "main-table",
                "主桌",
            )
        )

    if not requested:
        (output_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    for leftover in output_dir.glob("table-*.jpg"):
        leftover.unlink()

    source_copy_suffix = ".jpg" if source.suffix.lower() in {".jpg", ".jpeg"} else source.suffix.lower()
    if source_copy_suffix:
        shutil.copyfile(source, output_dir / f"source-floor-plan{source_copy_suffix}")


if __name__ == "__main__":
    main()
