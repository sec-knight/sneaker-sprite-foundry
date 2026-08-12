#!/usr/bin/env python3
"""Prepare review-only Forest Wisp runtime-size candidates.

This intentionally does not use the asset manifest and cannot promote a
candidate.  It is a presentation-reference sizing comparison only.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image, ImageDraw

import prepare_runtime_candidate as candidate_prep
import sprite_foundry as foundry


ROOT = Path(__file__).resolve().parents[1]
SIZES = ((12, (16, 16)), (16, (24, 24)), (20, (24, 24)))
BACKGROUND = (20, 32, 24, 255)


@dataclass(frozen=True)
class WispMetrics:
    nominal_visual_height: int
    runtime_cell: tuple[int, int]
    visible_bounds: tuple[int, int, int, int]
    visual_height: int
    visual_width: int
    alpha_extrema: tuple[int, int]
    fractional_alpha_pixels: int
    visible_color_count: int
    occupancy_percent: float
    eyes_distinct: bool
    leaf_fins_distinct: bool
    flame_silhouette_readable: bool
    automated_readability: bool
    output: str
    preview: str


def _opaque_components(mask: Image.Image) -> list[tuple[int, int, int, int]]:
    """Return 4-connected component bounds for a binary mask."""
    width, height = mask.size
    data = mask.load()
    seen = bytearray(width * height)
    boxes: list[tuple[int, int, int, int]] = []
    for y in range(height):
        for x in range(width):
            index = y * width + x
            if seen[index] or not data[x, y]:
                continue
            stack = [(x, y)]
            seen[index] = 1
            points = []
            while stack:
                px, py = stack.pop()
                points.append((px, py))
                for nx, ny in ((px - 1, py), (px + 1, py), (px, py - 1), (px, py + 1)):
                    next_index = ny * width + nx
                    if 0 <= nx < width and 0 <= ny < height and not seen[next_index] and data[nx, ny]:
                        seen[next_index] = 1
                        stack.append((nx, ny))
            boxes.append((min(px for px, _ in points), min(py for _, py in points),
                          max(px for px, _ in points) + 1, max(py for _, py in points) + 1))
    return boxes


def _readability(cell: Image.Image) -> tuple[bool, bool, bool]:
    """A conservative geometry-and-colour heuristic; it does not approve art."""
    rgba = cell.convert("RGBA")
    width, height = rgba.size
    # The reference's eyes are the only near-black opaque detail in the lower centre.
    eye_mask = Image.new("1", cell.size)
    leaf_mask = Image.new("1", cell.size)
    for y in range(height):
        for x in range(width):
            red, green, blue, alpha = rgba.getpixel((x, y))
            if alpha < 96:
                continue
            if y >= height * 0.40 and y <= height * 0.86 and red < 88 and green < 88 and blue < 88:
                eye_mask.putpixel((x, y), 1)
            # The two fins occupy opposing outer-middle regions and are green-dominant.
            if height * 0.35 <= y <= height * 0.82 and green > red * 1.15 and green > blue * 1.05:
                leaf_mask.putpixel((x, y), 1)
    eyes = [box for box in _opaque_components(eye_mask) if (box[2] - box[0]) * (box[3] - box[1]) >= 1]
    left_leaf = any(box[0] < width * 0.32 and box[2] - box[0] >= 1 for box in _opaque_components(leaf_mask))
    right_leaf = any(box[2] > width * 0.68 and box[2] - box[0] >= 1 for box in _opaque_components(leaf_mask))
    visual = foundry.alpha_bbox(cell)
    flame = visual is not None and visual[1] < height * 0.25 and visual[3] == height and (visual[2] - visual[0]) >= 4
    return len(eyes) >= 2, left_leaf and right_leaf, flame


def prepare_one(source: Image.Image, nominal_height: int, cell_size: tuple[int, int], output_dir: Path) -> WispMetrics:
    cell, report = candidate_prep.prepare_runtime_candidate(source, cell_size, nominal_height)
    bounds = foundry.alpha_bbox(cell)
    if bounds is None:
        raise foundry.FoundryError("Prepared Wisp candidate is empty.")
    output = output_dir / f"wisp_{nominal_height}px_review.png"
    preview = output_dir / f"wisp_{nominal_height}px_review_8x.png"
    output_dir.mkdir(parents=True, exist_ok=True)
    cell.save(output)
    candidate_prep.make_preview(cell, preview)
    alpha = cell.getchannel("A")
    alpha_data = list(alpha.get_flattened_data())
    visible = [pixel for pixel in cell.get_flattened_data() if pixel[3] > 0]
    eyes, fins, flame = _readability(cell)
    return WispMetrics(
        nominal_visual_height=nominal_height,
        runtime_cell=cell.size,
        visible_bounds=bounds,
        visual_height=bounds[3] - bounds[1],
        visual_width=bounds[2] - bounds[0],
        alpha_extrema=alpha.getextrema(),
        fractional_alpha_pixels=sum(0 < value < 255 for value in alpha_data),
        visible_color_count=len(set(visible)),
        occupancy_percent=round(100 * sum(value > 0 for value in alpha_data) / (cell.width * cell.height), 2),
        eyes_distinct=eyes,
        leaf_fins_distinct=fins,
        flame_silhouette_readable=flame,
        automated_readability=eyes and fins and flame,
        output=str(output),
        preview=str(preview),
    )


def _paste_scaled(canvas: Image.Image, image: Image.Image, xy: tuple[int, int], scale: int) -> None:
    canvas.alpha_composite(image.resize((image.width * scale, image.height * scale), Image.Resampling.NEAREST), xy)


def build_comparison(cells: list[Image.Image], metrics: list[WispMetrics], guardian: Image.Image, output: Path) -> None:
    canvas = Image.new("RGBA", (1080, 660), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    draw.text((24, 18), "Forest Wisp runtime sizing - REVIEW ONLY (no candidate is approved)", fill="#fff5df")
    draw.text((24, 42), "Native cells, 8x nearest-neighbor, and approximate phone gameplay scale beside Guardian 64x64.", fill="#c6d8c4")
    columns = (180, 520, 860)
    for index, (cell, metric, center) in enumerate(zip(cells, metrics, columns, strict=True)):
        draw.text((center - 48, 85), f"{metric.nominal_visual_height}px nominal", fill="#ffffff")
        draw.text((center - 48, 105), f"{cell.width}x{cell.height} cell", fill="#c6d8c4")
        native_x = center - cell.width // 2
        canvas.alpha_composite(cell, (native_x, 135))
        draw.rectangle((native_x - 1, 134, native_x + cell.width, 135 + cell.height), outline="#738b78")
        draw.text((center - 22, 165), "native", fill="#c6d8c4")
        enlarged_x = center - cell.width * 4
        _paste_scaled(canvas, cell, (enlarged_x, 195), 8)
        draw.rectangle((enlarged_x - 1, 194, enlarged_x + cell.width * 8, 195 + cell.height * 8), outline="#ffffff")
        draw.text((center - 42, 405), "8x nearest", fill="#c6d8c4")
        # Approximate phone gameplay: Guardian is 44 CSS px tall; Wisp uses 40% of that height.
        guardian_phone = guardian.resize((44, 44), Image.Resampling.NEAREST)
        wisp_height = max(1, round(44 * metric.nominal_visual_height / 60))
        wisp_phone = cell.resize((max(1, round(cell.width * wisp_height / cell.height)), wisp_height), Image.Resampling.NEAREST)
        canvas.alpha_composite(guardian_phone, (center - 70, 465))
        canvas.alpha_composite(wisp_phone, (center + 32, 465 + 44 - wisp_height))
        draw.text((center - 78, 520), "Guardian       Wisp", fill="#c6d8c4")
        draw.text((center - 70, 542), f"phone: 44px / {wisp_height}px", fill="#c6d8c4")
    draw.text((24, 610), "Heuristic checks are diagnostic only; evaluate eyes, core, flame, and leaf fins by human review.", fill="#e8e2d0")
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output)


def build_gameplay_comparison(cells: list[Image.Image], metrics: list[WispMetrics], guardian: Image.Image, output: Path) -> None:
    """Render the phone-scale row independently for focused review."""
    canvas = Image.new("RGBA", (900, 250), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    draw.text((24, 18), "Guardian + Forest Wisp approximate phone gameplay scale - REVIEW ONLY", fill="#fff5df")
    draw.text((24, 42), "Guardian: 44px tall. Wisp target heights derive from the Guardian 60px nominal visual height.", fill="#c6d8c4")
    for center, cell, metric in zip((150, 450, 750), cells, metrics, strict=True):
        guardian_phone = guardian.resize((44, 44), Image.Resampling.NEAREST)
        wisp_height = max(1, round(44 * metric.nominal_visual_height / 60))
        wisp_phone = cell.resize((max(1, round(cell.width * wisp_height / cell.height)), wisp_height), Image.Resampling.NEAREST)
        canvas.alpha_composite(guardian_phone, (center - 55, 125))
        canvas.alpha_composite(wisp_phone, (center + 28, 125 + 44 - wisp_height))
        draw.text((center - 80, 190), f"{metric.nominal_visual_height}px Wisp ({wisp_height}px phone)", fill="#ffffff")
        draw.text((center - 72, 212), "Guardian 44px", fill="#c6d8c4")
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output)


def run(root: Path = ROOT) -> list[WispMetrics]:
    source_path = root / "art/references/wisp/wisp_front_canonical.png"
    guardian_path = root / "art/generated/source/guardian_front_runtime_approved.png"
    output_dir = root / "art/generated/review/wisp"
    with Image.open(source_path) as source:
        cells_and_metrics = []
        for height, cell_size in SIZES:
            metrics = prepare_one(source, height, cell_size, output_dir)
            with Image.open(metrics.output) as prepared:
                cells_and_metrics.append((prepared.convert("RGBA"), metrics))
    cells, metrics = zip(*cells_and_metrics, strict=True)
    with Image.open(guardian_path) as guardian:
        build_comparison(list(cells), list(metrics), guardian.convert("RGBA"), output_dir / "wisp_size_comparison.png")
        build_gameplay_comparison(list(cells), list(metrics), guardian.convert("RGBA"), output_dir / "wisp_guardian_gameplay_scale_comparison.png")
    (output_dir / "wisp_size_comparison_report.json").write_text(json.dumps({
        "status": "review_only_not_approved",
        "presentation_reference": str(source_path),
        "guardian_runtime_master": str(guardian_path),
        "candidates": [asdict(metric) for metric in metrics],
        "comparison": str(output_dir / "wisp_size_comparison.png"),
        "gameplay_scale_comparison": str(output_dir / "wisp_guardian_gameplay_scale_comparison.png"),
    }, indent=2), encoding="utf-8")
    return list(metrics)


if __name__ == "__main__":
    print(json.dumps([asdict(metric) for metric in run()], indent=2))
