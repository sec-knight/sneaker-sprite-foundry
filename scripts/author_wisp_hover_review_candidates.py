#!/usr/bin/env python3
"""Create review-only Wisp hover pose studies outside the runtime packing pipeline."""

from __future__ import annotations

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "art/generated/source/wisp_front_runtime_approved.png"
REVIEW = ROOT / "art/generated/review/wisp"


def _green(pixel: tuple[int, int, int, int]) -> bool:
    red, green, blue, alpha = pixel
    return alpha > 0 and green > red * 1.18 and green > blue * 1.05


def _move_pixel(destination: Image.Image, pixel: tuple[int, int, int, int], x: int, y: int) -> None:
    if 0 <= x < destination.width and 0 <= y < destination.height:
        destination.putpixel((x, y), pixel)


def stretch_rise(master: Image.Image) -> Image.Image:
    """A buoyant flame stretch while leaving the core and eyes recognizable."""
    output = Image.new("RGBA", master.size, (0, 0, 0, 0))
    for y in range(master.height):
        for x in range(master.width):
            pixel = master.getpixel((x, y))
            if not _green(pixel) or y >= 14:
                _move_pixel(output, pixel, x, y)
                continue
            if x <= 6 or x >= 18:  # Side fins rise with the hover.
                _move_pixel(output, pixel, x, y - 1)
                continue
            rise = 2 if y <= 8 else 1
            narrowed_x = round(12 + (x - 12) * 0.88)
            _move_pixel(output, pixel, narrowed_x, y - rise)
    return output


def tilt_flair(master: Image.Image) -> Image.Image:
    """A playful leftward local lean with a curling flame and asymmetric fins."""
    output = Image.new("RGBA", master.size, (0, 0, 0, 0))
    for y in range(master.height):
        for x in range(master.width):
            pixel = master.getpixel((x, y))
            if not _green(pixel):
                # The face-core and eyes travel together by one pixel, retaining their relationship.
                if 14 <= y <= 18:
                    _move_pixel(output, pixel, x - 1, y)
                else:
                    _move_pixel(output, pixel, x, y)
                continue
            if y <= 9:  # Curl the flame left rather than rotating the full bitmap.
                _move_pixel(output, pixel, x - 2, y)
            elif x <= 6 and 10 <= y <= 16:
                _move_pixel(output, pixel, x - 1, y - 1)
            elif x >= 18 and 10 <= y <= 16:
                _move_pixel(output, pixel, x + 1, y + 1)
            elif 10 <= y <= 18:
                _move_pixel(output, pixel, x - 1, y)
            else:
                _move_pixel(output, pixel, x, y)
    return output


def main() -> None:
    with Image.open(MASTER) as source:
        master = source.convert("RGBA")
    if master.size != (24, 24):
        raise ValueError(f"Expected 24x24 approved Wisp master, got {master.size}.")
    REVIEW.mkdir(parents=True, exist_ok=True)
    stretch_path = REVIEW / "wisp_hover_stretch.png"
    tilt_path = REVIEW / "wisp_hover_tilt.png"
    if stretch_path.exists() != tilt_path.exists():
        raise FileExistsError("Only one Wisp review candidate exists; refuse to replace or infer the missing peer.")
    if stretch_path.exists():
        with Image.open(stretch_path) as source:
            stretch = source.convert("RGBA")
        with Image.open(tilt_path) as source:
            tilt = source.convert("RGBA")
    else:
        stretch = stretch_rise(master)
        tilt = tilt_flair(master)
        stretch.save(stretch_path)
        tilt.save(tilt_path)
    frames = [master, stretch, master, tilt]
    enlarged = [frame.resize((192, 192), Image.Resampling.NEAREST) for frame in frames]
    sheet = Image.new("RGBA", (192 * 4, 192), (20, 32, 24, 255))
    for index, frame in enumerate(enlarged):
        sheet.alpha_composite(frame, (index * 192, 0))
    sheet.save(REVIEW / "wisp_hover_review_sheet.png")
    enlarged[0].save(REVIEW / "wisp_hover_review.gif", save_all=True, append_images=enlarged[1:],
                     duration=180, loop=0, disposal=2)
    print(stretch_path)
    print(tilt_path)
    print(REVIEW / "wisp_hover_review_sheet.png")
    print(REVIEW / "wisp_hover_review.gif")


if __name__ == "__main__":
    main()
