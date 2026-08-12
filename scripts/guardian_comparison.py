#!/usr/bin/env python3
"""Build a development-only nearest-neighbor comparison against the live Plushy prototype."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw

import sprite_foundry as foundry


PHONE_SIZE = (88, 106)  # Exact CSS draw size in sneaker.games/plushy/index.html.


def decode_prototype_guardian(sprite_js: Path) -> Image.Image:
    text = sprite_js.read_text(encoding="utf-8")
    start = text.index('{"guardian"')
    end = text.rindex('});') + 1
    guardian = json.loads(text[start:end])["guardian"]
    image = Image.new("RGBA", (guardian["w"], guardian["h"]), (0, 0, 0, 0))
    pixels: list[tuple[int, int, int, int]] = []
    for run in guardian["r"].split(","):
        count, palette_index = int(run[:-1], 36), int(run[-1], 16)
        colour = guardian["p"][palette_index]
        pixels.extend([tuple(int(colour[index:index + 2], 16) for index in (1, 3, 5, 7))] * count)
    if len(pixels) != image.width * image.height:
        raise ValueError("Live guardian RLE does not match its declared dimensions.")
    image.putdata(pixels)
    return image


def body_bbox(image: Image.Image) -> tuple[int, int, int, int]:
    """Find the dense lower body envelope, excluding sparse antler rows."""
    alpha = image.convert("RGBA").getchannel("A")
    visual = alpha.getbbox()
    if visual is None:
        raise ValueError("Sprite has no visible pixels.")
    left, top, right, bottom = visual
    row_counts = [sum(alpha.getpixel((x, y)) > 0 for x in range(left, right)) for y in range(top, bottom)]
    threshold = max(1, round(max(row_counts) * 0.75))
    dense_rows = [y for y, count in zip(range(top, bottom), row_counts, strict=True) if count >= threshold]
    if not dense_rows:
        return visual
    body_top = dense_rows[0]
    body_pixels = [(x, y) for y in dense_rows for x in range(left, right)
                   if alpha.getpixel((x, y)) > 0]
    return (min(x for x, _ in body_pixels), body_top, max(x for x, _ in body_pixels) + 1, bottom)


def draw_sample(canvas: Image.Image, image: Image.Image, center_x: int, top_y: int, size: tuple[int, int], title: str,
                visual: tuple[int, int, int, int] | None = None, body: tuple[int, int, int, int] | None = None) -> None:
    draw = ImageDraw.Draw(canvas)
    scaled = image.resize(size, Image.Resampling.NEAREST)
    left = center_x - size[0] // 2
    canvas.alpha_composite(scaled, (left, top_y))
    draw.text((left, top_y - 14), title, fill="#e8e2d0")
    if visual:
        sx, sy = size[0] / image.width, size[1] / image.height
        draw.rectangle((left + visual[0] * sx, top_y + visual[1] * sy,
                        left + visual[2] * sx - 1, top_y + visual[3] * sy - 1), outline="#ff6767", width=1)
    if body:
        sx, sy = size[0] / image.width, size[1] / image.height
        draw.rectangle((left + body[0] * sx, top_y + body[1] * sy,
                        left + body[2] * sx - 1, top_y + body[3] * sy - 1), outline="#63d8ff", width=1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prototype-js", type=Path, required=True)
    parser.add_argument("--canonical", type=Path, default=Path("art/generated/source/guardian_canonical_front.png"))
    parser.add_argument("--foundry", type=Path, default=Path("art/generated/normalized/guardian_idle.png"))
    parser.add_argument("--out", type=Path, default=Path("art/generated/comparisons/guardian_scale_comparison.png"))
    args = parser.parse_args()

    prototype = decode_prototype_guardian(args.prototype_js)
    canonical = Image.open(args.canonical).convert("RGBA")
    foundry_sheet = Image.open(args.foundry).convert("RGBA")
    foundry_frame = foundry_sheet.crop((0, 0, 64, 64))
    canonical_subject = foundry.prepare_canonical_source(canonical)
    samples = [("Live prototype", prototype), ("Canonical presentation", canonical), ("Foundry frame 1", foundry_frame)]

    canvas = Image.new("RGBA", (1440, 1010), "#142018")
    draw = ImageDraw.Draw(canvas)
    draw.text((24, 18), "Guardian scale comparison — red: complete visual bounds; cyan: dense body envelope", fill="#fff5df")
    draw.text((24, 38), "All enlargements use nearest-neighbor. Phone row uses the live prototype draw size: 88 × 106 CSS px.", fill="#c6d8c4")
    columns = (240, 720, 1200)
    rows = (("Native", 82, lambda image: image.size), ("2× nearest", 380, lambda image: (image.width * 2, image.height * 2)),
            ("Phone gameplay", 890, lambda image: PHONE_SIZE))
    for center_x, (name, image) in zip(columns, samples, strict=True):
        visual = image.getchannel("A").getbbox()
        if name == "Canonical presentation":
            visual = (14, 8, 191, 223)  # Subject bounds from deterministic canonical preparation.
        body = body_bbox(canonical_subject if name == "Canonical presentation" else image)
        if name == "Canonical presentation":
            body = (body[0] + 14, body[1] + 8, body[2] + 14, body[3] + 8)
        draw.text((center_x - 130, 62), name, fill="#ffffff")
        for label, top, size_for in rows:
            size = size_for(image)
            draw_sample(canvas, image, center_x, top, size, label, visual, body)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(args.out)
    print(args.out)


if __name__ == "__main__":
    main()
