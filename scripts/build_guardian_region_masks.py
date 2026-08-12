#!/usr/bin/env python3
"""Create the minimal reviewed-region masks for Guardian Idle v2 from master pixels."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "art/generated/source/guardian_front_runtime_approved.png"
OUT = ROOT / "art/masks/guardian"


def mask_from(predicate, master: Image.Image) -> Image.Image:
    mask = Image.new("L", master.size)
    pixels = master.convert("RGBA").load()
    output = mask.load()
    for y in range(master.height):
        for x in range(master.width):
            if predicate(x, y, pixels[x, y]):
                output[x, y] = 255
    return mask


def main() -> None:
    master = Image.open(MASTER).convert("RGBA")
    if master.size != (64, 64):
        raise SystemExit("Guardian master must be 64x64 before masks can be authored.")
    alpha = master.getchannel("A")
    # Foliage only: green-dominant antler pixels, leaving branches and face stationary.
    leaf = lambda pixel: pixel[3] and pixel[1] > pixel[0] * 1.18 and pixel[1] > pixel[2] * 1.32
    left = mask_from(lambda x, y, pixel: y < 33 and x < 32 and leaf(pixel), master)
    right = mask_from(lambda x, y, pixel: y < 33 and x >= 32 and leaf(pixel), master)
    body = mask_from(lambda x, y, pixel: alpha.getpixel((x, y)) and 23 <= y <= 46 and 15 <= x <= 48, master)
    # Regions are disjoint: foliage wins at the head/antler boundary.
    body_pixels = body.load()
    for y in range(master.height):
        for x in range(master.width):
            if left.getpixel((x, y)) or right.getpixel((x, y)):
                body_pixels[x, y] = 0
    hem = mask_from(lambda x, y, pixel: alpha.getpixel((x, y)) and 48 <= y <= 59 and (x <= 22 or x >= 41), master)
    OUT.mkdir(parents=True, exist_ok=True)
    masks = {"body": body, "left_antler_foliage": left, "right_antler_foliage": right, "cloak_hem": hem}
    for name, mask in masks.items():
        if mask.getbbox() is None:
            raise SystemExit(f"{name} mask is empty")
        mask.save(OUT / f"{name}.png")
    colours = {"body": (86, 194, 255, 170), "left_antler_foliage": (255, 96, 155, 190),
               "right_antler_foliage": (255, 207, 74, 190), "cloak_hem": (151, 103, 255, 190)}
    debug = master.copy()
    for name, mask in masks.items():
        overlay = Image.new("RGBA", master.size, colours[name])
        debug.alpha_composite(Image.composite(overlay, Image.new("RGBA", master.size), mask))
    debug = debug.resize((512, 512), Image.Resampling.NEAREST)
    ImageDraw.Draw(debug).rectangle((0, 0, 511, 511), outline=(255, 255, 255, 130), width=1)
    review = ROOT / "art/generated/review/guardian_idle_region_masks_preview.png"
    review.parent.mkdir(parents=True, exist_ok=True)
    debug.save(review)
    print(review)


if __name__ == "__main__":
    main()
