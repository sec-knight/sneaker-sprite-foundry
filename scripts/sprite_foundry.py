#!/usr/bin/env python3
"""Deterministic normalizer for the Sneaker Sprite Foundry vertical slice."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]


class FoundryError(Exception):
    """An input or validation problem that should be shown to the artist."""


@dataclass(frozen=True)
class SpriteSpec:
    name: str
    source: Path
    frames: int
    cell_width: int
    cell_height: int
    nominal_height: int
    anchor: str


@dataclass(frozen=True)
class ValidationReport:
    passed: bool
    reasons: tuple[str, ...]


def load_spec(manifest_path: Path, asset_name: str, root: Path = ROOT) -> SpriteSpec:
    with manifest_path.open(encoding="utf-8") as handle:
        assets = (yaml.safe_load(handle) or {}).get("assets", {})
    if asset_name not in assets:
        raise FoundryError(f"Asset '{asset_name}' is not defined in {manifest_path}.")
    asset = assets[asset_name]
    cell_width, cell_height = asset["runtime_cell"]
    if asset.get("anchor") != "bottom-center":
        raise FoundryError("This foundry slice requires a bottom-center anchor.")
    return SpriteSpec(
        name=asset_name,
        source=root / asset["source"],
        frames=int(asset["frames"]),
        cell_width=int(cell_width),
        cell_height=int(cell_height),
        nominal_height=int(asset["nominal_character_height"]),
        anchor=asset["anchor"],
    )


def alpha_bbox(image: Image.Image) -> tuple[int, int, int, int] | None:
    return image.getchannel("A").getbbox()


def _runs(values: Iterable[bool]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate(values):
        if value and start is None:
            start = index
        elif not value and start is not None:
            runs.append((start, index))
            start = None
    if start is not None:
        runs.append((start, index + 1))
    return runs


def extract_frames(source: Image.Image, expected_frames: int) -> list[Image.Image]:
    """Split a transparent horizontal source strip at fully transparent columns."""
    source = source.convert("RGBA")
    alpha = source.getchannel("A")
    occupied_columns = [alpha.crop((x, 0, x + 1, source.height)).getbbox() is not None
                        for x in range(source.width)]
    columns = _runs(occupied_columns)
    if len(columns) != expected_frames:
        raise FoundryError(
            f"Expected {expected_frames} clearly separated source frames, found {len(columns)}. "
            "Use a transparent background and leave at least one fully transparent column between frames."
        )
    frames: list[Image.Image] = []
    for left, right in columns:
        frame = source.crop((left, 0, right, source.height))
        bbox = alpha_bbox(frame)
        if bbox is None:
            raise FoundryError("A source frame has no visible pixels.")
        frames.append(frame.crop(bbox))
    return frames


def normalize_frames(frames: list[Image.Image], spec: SpriteSpec) -> list[Image.Image]:
    if len(frames) != spec.frames:
        raise FoundryError(f"Expected {spec.frames} frames, received {len(frames)}.")
    bounds = [alpha_bbox(frame) for frame in frames]
    if any(bound is None for bound in bounds):
        raise FoundryError("Cannot normalize an empty frame.")
    trimmed = [frame.crop(bound) for frame, bound in zip(frames, bounds, strict=True)]
    max_width = max(frame.width for frame in trimmed)
    max_height = max(frame.height for frame in trimmed)
    # One animation-wide scale prevents frame-to-frame character-size drift.
    scale = min(spec.nominal_height / max_height, (spec.cell_width - 4) / max_width)
    if scale <= 0:
        raise FoundryError("Could not calculate a positive shared scale.")
    normalized: list[Image.Image] = []
    for frame in trimmed:
        width = max(1, round(frame.width * scale))
        height = max(1, round(frame.height * scale))
        resized = frame.resize((width, height), Image.Resampling.LANCZOS)
        cell = Image.new("RGBA", (spec.cell_width, spec.cell_height), (0, 0, 0, 0))
        x = (spec.cell_width - width) // 2
        y = spec.cell_height - height
        cell.alpha_composite(resized, (x, y))
        normalized.append(cell)
    return normalized


def make_sheet(cells: list[Image.Image], spec: SpriteSpec) -> Image.Image:
    sheet = Image.new("RGBA", (spec.cell_width * spec.frames, spec.cell_height), (0, 0, 0, 0))
    for index, cell in enumerate(cells):
        sheet.alpha_composite(cell, (index * spec.cell_width, 0))
    return sheet


def validate(cells: list[Image.Image], sheet: Image.Image, spec: SpriteSpec) -> ValidationReport:
    reasons: list[str] = []
    if len(cells) != spec.frames:
        reasons.append(f"expected {spec.frames} cells, got {len(cells)}")
    if sheet.size != (spec.cell_width * spec.frames, spec.cell_height):
        reasons.append(f"sheet is {sheet.size}, expected {(spec.cell_width * spec.frames, spec.cell_height)}")
    if sheet.getchannel("A").getextrema()[0] != 0:
        reasons.append("sheet has no transparent pixels")
    heights: list[int] = []
    centers: list[float] = []
    for index, cell in enumerate(cells, start=1):
        if cell.size != (spec.cell_width, spec.cell_height):
            reasons.append(f"cell {index} has wrong dimensions")
            continue
        bbox = alpha_bbox(cell)
        if bbox is None:
            reasons.append(f"cell {index} is fully transparent")
            continue
        left, top, right, bottom = bbox
        if left < 0 or top < 0 or right > spec.cell_width or bottom > spec.cell_height:
            reasons.append(f"cell {index} overflows its bounds")
        if bottom != spec.cell_height:
            reasons.append(f"cell {index} is not bottom anchored")
        centers.append((left + right) / 2)
        heights.append(bottom - top)
    if centers and any(abs(center - spec.cell_width / 2) > 2 for center in centers):
        reasons.append("one or more cells are not center anchored")
    if heights:
        if max(heights) - min(heights) > 3:
            reasons.append("character bounds vary by more than 3 px across frames")
        if abs(max(heights) - spec.nominal_height) > 5:
            reasons.append(f"nominal height is {max(heights)} px, expected about {spec.nominal_height} px")
    return ValidationReport(not reasons, tuple(reasons))


def make_preview(cells: list[Image.Image], spec: SpriteSpec, preview_path: Path, gif_path: Path) -> None:
    scale = 4
    sheet = make_sheet(cells, spec)
    preview = sheet.resize((sheet.width * scale, sheet.height * scale), Image.Resampling.NEAREST)
    draw = ImageDraw.Draw(preview)
    for x in range(spec.cell_width * scale, preview.width, spec.cell_width * scale):
        draw.line((x, 0, x, preview.height), fill=(255, 255, 255, 80), width=1)
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview.save(preview_path)
    gif_frames = [cell.resize((spec.cell_width * scale, spec.cell_height * scale), Image.Resampling.NEAREST) for cell in cells]
    gif_frames[0].save(gif_path, save_all=True, append_images=gif_frames[1:], duration=180, loop=0, disposal=2)


def run(asset_name: str, root: Path = ROOT) -> ValidationReport:
    spec = load_spec(root / "art/manifests/sprites.yaml", asset_name, root)
    if not spec.source.exists():
        raise FoundryError(f"Source image is missing: place the four-frame strip at {spec.source}.")
    with Image.open(spec.source) as source_file:
        frames = extract_frames(source_file, spec.frames)
    cells = normalize_frames(frames, spec)
    sheet = make_sheet(cells, spec)
    report = validate(cells, sheet, spec)
    if not report.passed:
        raise FoundryError("Validation failed: " + "; ".join(report.reasons))
    normalized = root / "art/generated/normalized" / f"{spec.name}.png"
    normalized.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(normalized)
    make_preview(cells, spec, root / "art/generated/previews" / f"{spec.name}_preview.png",
                 root / "art/generated/previews" / f"{spec.name}.gif")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize one source sprite strip.")
    parser.add_argument("asset", help="asset key from art/manifests/sprites.yaml")
    args = parser.parse_args()
    try:
        report = run(args.asset)
    except (FoundryError, OSError, yaml.YAMLError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(f"PASS: {args.asset} normalized and validated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
