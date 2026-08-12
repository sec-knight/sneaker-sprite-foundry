#!/usr/bin/env python3
"""Prepare a generated runtime candidate for human review; never promote it to a master."""

from __future__ import annotations

import argparse
import json
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image, ImageDraw

import sprite_foundry as foundry


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class PreparationReport:
    input_dimensions: tuple[int, int]
    isolated_bounds: tuple[int, int, int, int]
    output_dimensions: tuple[int, int]
    output_visual_bounds: tuple[int, int, int, int]
    visual_height: int
    input_alpha_extrema: tuple[int, int]
    output_alpha_extrema: tuple[int, int]
    input_color_count: int
    output_color_count: int
    touches_cell_boundary: bool
    output: str
    preview: str


def _largest_opaque_component(image: Image.Image, alpha_threshold: int = 128) -> Image.Image:
    """Keep the largest high-confidence alpha component from a generated candidate."""
    image = image.convert("RGBA")
    width, height = image.size
    alpha = image.getchannel("A")
    alpha_pixels = alpha.load()
    visited = bytearray(width * height)
    largest: list[int] = []
    for y in range(height):
        for x in range(width):
            index = y * width + x
            if visited[index] or alpha_pixels[x, y] < alpha_threshold:
                continue
            visited[index] = 1
            component: list[int] = []
            pending: deque[tuple[int, int]] = deque([(x, y)])
            while pending:
                current_x, current_y = pending.popleft()
                component.append(current_y * width + current_x)
                for next_x, next_y in ((current_x - 1, current_y), (current_x + 1, current_y),
                                      (current_x, current_y - 1), (current_x, current_y + 1)):
                    if not (0 <= next_x < width and 0 <= next_y < height):
                        continue
                    next_index = next_y * width + next_x
                    if not visited[next_index] and alpha_pixels[next_x, next_y] >= alpha_threshold:
                        visited[next_index] = 1
                        pending.append((next_x, next_y))
            if len(component) > len(largest):
                largest = component
    if not largest:
        raise foundry.FoundryError("Runtime candidate has no opaque Guardian component.")
    output_alpha = bytearray(width * height)
    for index in largest:
        output_alpha[index] = 255
    image.putalpha(Image.frombytes("L", image.size, bytes(output_alpha)))
    return image


def _resize_premultiplied(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Reduce using BOX area sampling in premultiplied alpha to prevent dark matte fringes."""
    image = image.convert("RGBA")
    width, height = image.size
    red, green, blue, alpha = image.split()
    alpha_data = alpha.tobytes()
    channels = []
    for channel in (red, green, blue):
        data = bytes(value * opacity // 255 for value, opacity in zip(channel.tobytes(), alpha_data, strict=True))
        channels.append(Image.frombytes("L", (width, height), data).resize(size, Image.Resampling.BOX))
    alpha = alpha.resize(size, Image.Resampling.BOX)
    alpha_data = alpha.tobytes()
    restored = []
    for channel in channels:
        restored.append(Image.frombytes("L", size, bytes(
            0 if opacity == 0 else min(255, value * 255 // opacity)
            for value, opacity in zip(channel.tobytes(), alpha_data, strict=True)
        )))
    return Image.merge("RGBA", (*restored, alpha))


def prepare_runtime_candidate(source: Image.Image, cell_size: tuple[int, int] = (64, 64), visual_height: int = 60) -> tuple[Image.Image, PreparationReport]:
    """Transform a high-resolution candidate into a transparent review-only runtime cell."""
    source = source.convert("RGBA")
    input_colors = len(set(source.get_flattened_data()))
    isolated = _largest_opaque_component(source)
    bounds = foundry.alpha_bbox(isolated)
    if bounds is None:
        raise foundry.FoundryError("Runtime candidate could not be isolated.")
    sprite = isolated.crop(bounds)
    target_width = max(1, round(sprite.width * visual_height / sprite.height))
    if target_width > cell_size[0]:
        target_width = cell_size[0]
        visual_height = max(1, round(sprite.height * target_width / sprite.width))
    reduced = _resize_premultiplied(sprite, (target_width, visual_height))
    cell = Image.new("RGBA", cell_size, (0, 0, 0, 0))
    cell.alpha_composite(reduced, ((cell_size[0] - target_width) // 2, cell_size[1] - visual_height))
    output_bounds = foundry.alpha_bbox(cell)
    if output_bounds is None:
        raise foundry.FoundryError("Prepared runtime candidate is empty.")
    touches = output_bounds[0] == 0 or output_bounds[1] == 0 or output_bounds[2] == cell_size[0] or output_bounds[3] == cell_size[1]
    report = PreparationReport(
        input_dimensions=source.size,
        isolated_bounds=bounds,
        output_dimensions=cell.size,
        output_visual_bounds=output_bounds,
        visual_height=output_bounds[3] - output_bounds[1],
        input_alpha_extrema=source.getchannel("A").getextrema(),
        output_alpha_extrema=cell.getchannel("A").getextrema(),
        input_color_count=input_colors,
        output_color_count=len(set(cell.get_flattened_data())),
        touches_cell_boundary=touches,
        output="",
        preview="",
    )
    return cell, report


def make_preview(cell: Image.Image, path: Path) -> None:
    preview = cell.resize((cell.width * 8, cell.height * 8), Image.Resampling.NEAREST)
    ImageDraw.Draw(preview).rectangle((0, 0, preview.width - 1, preview.height - 1), outline=(255, 255, 255, 96))
    path.parent.mkdir(parents=True, exist_ok=True)
    preview.save(path)


def run(asset_name: str, root: Path = ROOT) -> PreparationReport:
    spec = foundry.load_spec(root / "art/manifests/sprites.yaml", asset_name, root)
    if spec.runtime_candidate is None:
        raise foundry.FoundryError(f"Asset '{asset_name}' does not declare runtime_candidate.")
    if not spec.runtime_candidate.exists():
        raise foundry.FoundryError(f"Runtime candidate is missing: {spec.runtime_candidate}.")
    with Image.open(spec.runtime_candidate) as source:
        cell, report = prepare_runtime_candidate(source, (spec.cell_width, spec.cell_height), spec.nominal_height)
    output = root / "art/generated/review" / f"{asset_name.replace('_idle', '')}_runtime.png"
    preview = root / "art/generated/review" / f"{asset_name.replace('_idle', '')}_runtime_preview.png"
    if output.resolve() == spec.runtime_master.resolve():
        raise foundry.FoundryError("Review candidate path must never equal the approved runtime-master path.")
    output.parent.mkdir(parents=True, exist_ok=True)
    cell.save(output)
    make_preview(cell, preview)
    report_path = output.with_suffix(".json")
    final_report = PreparationReport(**(asdict(report) | {"output": str(output), "preview": str(preview)}))
    report_path.write_text(json.dumps(asdict(final_report), indent=2), encoding="utf-8")
    return final_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare a runtime candidate for human review.")
    parser.add_argument("asset", help="asset key from art/manifests/sprites.yaml")
    args = parser.parse_args()
    try:
        report = run(args.asset)
    except (foundry.FoundryError, OSError) as error:
        print(f"FAIL: {error}")
        return 1
    print(json.dumps(asdict(report), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
