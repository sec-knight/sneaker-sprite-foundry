#!/usr/bin/env python3
"""Deterministic normalizer for the Sneaker Sprite Foundry vertical slice."""

from __future__ import annotations

import argparse
import sys
from collections import deque
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
    source_mode: str = "strip"
    motion_offsets: tuple[int, ...] = (0, -1, 0, 0)
    presentation_reference: Path | None = None
    runtime_master: Path | None = None


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
    source_mode = asset.get("source_mode", "strip")
    if source_mode not in {"strip", "canonical_derived", "runtime_master_derived"}:
        raise FoundryError(f"Unsupported source mode: {source_mode}.")
    runtime_master = root / asset["runtime_master"] if asset.get("runtime_master") else None
    presentation_reference = root / asset["presentation_reference"] if asset.get("presentation_reference") else None
    if source_mode == "runtime_master_derived" and runtime_master is None:
        raise FoundryError("runtime_master_derived assets must declare runtime_master.")
    return SpriteSpec(
        name=asset_name,
        source=runtime_master if runtime_master is not None else root / asset["source"],
        frames=int(asset["frames"]),
        cell_width=int(cell_width),
        cell_height=int(cell_height),
        nominal_height=int(asset["nominal_character_height"]),
        anchor=asset["anchor"],
        source_mode=source_mode,
        motion_offsets=tuple(int(offset) for offset in asset.get("motion", {}).get("offsets_y", (0,) * int(asset["frames"]))),
        presentation_reference=presentation_reference,
        runtime_master=runtime_master,
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
    """Split transparent strips or equally-slotted opaque reference exports."""
    source = source.convert("RGBA")
    alpha = source.getchannel("A")
    if alpha.getextrema()[0] == 255:
        if source.width % expected_frames:
            raise FoundryError(
                f"Opaque source width {source.width} cannot be divided into {expected_frames} equal frame slots."
            )
        slot_width = source.width // expected_frames
        frames: list[Image.Image] = []
        for index in range(expected_frames):
            slot = source.crop((index * slot_width, 0, (index + 1) * slot_width, source.height))
            foreground = _remove_edge_connected_background(slot)
            bounds = alpha_bbox(foreground)
            if bounds is None:  # Guarded in _remove_edge_connected_background; keeps type narrowing explicit.
                raise FoundryError("Opaque source slot contains no foreground after background removal.")
            frames.append(foreground.crop(bounds))
        return frames
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


def _remove_edge_connected_background(image: Image.Image, tolerance: int = 16) -> Image.Image:
    """Make a baked checkerboard/flat background transparent without touching enclosed art.

    Only pixels connected to a frame edge through small colour changes are removed.  This
    intentionally rejects opaque artwork that touches an edge rather than guessing at its
    silhouette, while accepting the equally-slotted reference exports used by this foundry.
    """
    image = image.convert("RGBA")
    width, height = image.size
    pixels = image.load()
    background = bytearray(width * height)
    pending: deque[tuple[int, int]] = deque()

    def add(x: int, y: int) -> None:
        index = y * width + x
        if not background[index]:
            background[index] = 1
            pending.append((x, y))

    for x in range(width):
        add(x, 0)
        add(x, height - 1)
    for y in range(1, height - 1):
        add(0, y)
        add(width - 1, y)

    while pending:
        x, y = pending.popleft()
        red, green, blue, _ = pixels[x, y]
        for next_x, next_y in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if not (0 <= next_x < width and 0 <= next_y < height):
                continue
            index = next_y * width + next_x
            if background[index]:
                continue
            next_pixel = pixels[next_x, next_y]
            if max(abs(next_pixel[0] - red), abs(next_pixel[1] - green), abs(next_pixel[2] - blue)) <= tolerance:
                background[index] = 1
                pending.append((next_x, next_y))

    alpha = image.getchannel("A")
    alpha_data = bytearray(alpha.tobytes())
    for index, is_background in enumerate(background):
        if is_background:
            alpha_data[index] = 0
    image.putalpha(Image.frombytes("L", image.size, bytes(alpha_data)))
    if alpha_bbox(image) is None:
        raise FoundryError("Opaque source slot contains no foreground after background removal.")
    return image


def _keep_largest_foreground_component(image: Image.Image) -> Image.Image:
    """Remove detached labels or debris from an isolated canonical presentation image."""
    image = image.convert("RGBA")
    width, height = image.size
    alpha = image.getchannel("A")
    alpha_pixels = alpha.load()
    visited = bytearray(width * height)
    largest: list[int] = []
    for y in range(height):
        for x in range(width):
            start = y * width + x
            if visited[start] or not alpha_pixels[x, y]:
                continue
            visited[start] = 1
            component: list[int] = []
            pending: deque[tuple[int, int]] = deque([(x, y)])
            while pending:
                current_x, current_y = pending.popleft()
                component.append(current_y * width + current_x)
                for next_x, next_y in ((current_x - 1, current_y), (current_x + 1, current_y),
                                      (current_x, current_y - 1), (current_x, current_y + 1)):
                    if not (0 <= next_x < width and 0 <= next_y < height):
                        continue
                    index = next_y * width + next_x
                    if not visited[index] and alpha_pixels[next_x, next_y]:
                        visited[index] = 1
                        pending.append((next_x, next_y))
            if len(component) > len(largest):
                largest = component
    if not largest:
        raise FoundryError("Canonical source contains no isolated foreground.")
    isolated_alpha = bytearray(width * height)
    for index in largest:
        isolated_alpha[index] = 255
    image.putalpha(Image.frombytes("L", image.size, bytes(isolated_alpha)))
    return image


def _is_light_background_colour(pixel: tuple[int, int, int, int]) -> bool:
    """Identify low-chroma warm pixels characteristic of the presentation-card matte."""
    red, green, blue, _ = pixel
    return red >= 175 and red >= green >= blue and red - blue <= 42


def _remove_background_fringe(image: Image.Image) -> Image.Image:
    """Peel low-chroma matte residue from the outside of an already isolated sprite.

    This is deliberately a hard, binary matte: it preserves crisp pixel edges and only
    removes warm, low-chroma pixels reachable from transparency.  The Guardian mask is
    distinctly more chromatic, so cream character pixels are retained.
    """
    image = image.convert("RGBA")
    width, height = image.size
    pixels = image.load()
    alpha = image.getchannel("A")
    alpha_pixels = alpha.load()
    remove = bytearray(width * height)
    pending: deque[tuple[int, int]] = deque()
    for y in range(height):
        for x in range(width):
            if not alpha_pixels[x, y] or not _is_light_background_colour(pixels[x, y]):
                continue
            if any(not (0 <= next_x < width and 0 <= next_y < height) or not alpha_pixels[next_x, next_y]
                   for next_x, next_y in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))):
                remove[y * width + x] = 1
                pending.append((x, y))
    while pending:
        x, y = pending.popleft()
        for next_x, next_y in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if not (0 <= next_x < width and 0 <= next_y < height):
                continue
            index = next_y * width + next_x
            if alpha_pixels[next_x, next_y] and not remove[index] and _is_light_background_colour(pixels[next_x, next_y]):
                remove[index] = 1
                pending.append((next_x, next_y))
    alpha_data = bytearray(alpha.tobytes())
    for index, should_remove in enumerate(remove):
        if should_remove:
            alpha_data[index] = 0
    image.putalpha(Image.frombytes("L", image.size, bytes(alpha_data)))
    return image


def prepare_canonical_source(source: Image.Image) -> Image.Image:
    """Isolate and trim one canonical character without altering the original source file."""
    source = source.convert("RGBA")
    if source.getchannel("A").getextrema()[0] == 255:
        source = _remove_edge_connected_background(source)
    source = _keep_largest_foreground_component(source)
    source = _remove_background_fringe(source)
    bounds = alpha_bbox(source)
    if bounds is None:
        raise FoundryError("Canonical source contains no foreground after preparation.")
    return source.crop(bounds)


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
        resized = frame.resize((width, height), Image.Resampling.NEAREST)
        cell = Image.new("RGBA", (spec.cell_width, spec.cell_height), (0, 0, 0, 0))
        x = (spec.cell_width - width) // 2
        y = spec.cell_height - height
        cell.alpha_composite(resized, (x, y))
        normalized.append(cell)
    return normalized


def _translate_cell(cell: Image.Image, offset_y: int, spec: SpriteSpec) -> Image.Image:
    """Translate a runtime cell by an exact integer number of pixels without resampling."""
    if not isinstance(offset_y, int):
        raise FoundryError("Canonical animation offsets must be integer pixels.")
    result = Image.new("RGBA", (spec.cell_width, spec.cell_height), (0, 0, 0, 0))
    result.alpha_composite(cell, (0, offset_y))
    return result


def derive_canonical_frames(source: Image.Image, spec: SpriteSpec) -> tuple[list[Image.Image], Image.Image]:
    """Create a neutral-rise-neutral-settle idle from one canonical character source."""
    if spec.frames != 4:
        raise FoundryError("canonical_derived breathing currently requires exactly 4 frames.")
    if len(spec.motion_offsets) != spec.frames:
        raise FoundryError("canonical_derived motion must provide one integer offset per frame.")
    prepared = prepare_canonical_source(source)
    seed = normalize_frames([prepared], SpriteSpec(
        spec.name, spec.source, 1, spec.cell_width, spec.cell_height, spec.nominal_height, spec.anchor,
        spec.source_mode, (0,),
    ))[0]
    return [_translate_cell(seed, offset, spec) for offset in spec.motion_offsets], prepared


def derive_runtime_master_frames(source: Image.Image, spec: SpriteSpec) -> list[Image.Image]:
    """Create idle frames from a reviewed runtime cell without resampling or matte processing."""
    if spec.frames != 4:
        raise FoundryError("runtime_master_derived animation currently requires exactly 4 frames.")
    if source.size != (spec.cell_width, spec.cell_height):
        raise FoundryError(
            f"Runtime master must already be {(spec.cell_width, spec.cell_height)}, got {source.size}."
        )
    master = source.convert("RGBA")
    if master.getchannel("A").getextrema()[0] != 0:
        raise FoundryError("Runtime master must include real transparent background pixels.")
    bounds = alpha_bbox(master)
    if bounds is None:
        raise FoundryError("Runtime master is fully transparent.")
    return [_translate_cell(master, offset, spec) for offset in spec.motion_offsets]


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
        expected_bottom = spec.cell_height
        if spec.source_mode in {"canonical_derived", "runtime_master_derived"} and index <= len(spec.motion_offsets):
            expected_bottom += spec.motion_offsets[index - 1]
        if bottom != expected_bottom:
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
    if spec.source_mode in {"canonical_derived", "runtime_master_derived"} and cells:
        for index, (cell, offset) in enumerate(zip(cells, spec.motion_offsets, strict=True), start=1):
            if cell.tobytes() != _translate_cell(cells[0], offset, spec).tobytes():
                reasons.append(f"cell {index} is not an exact integer translation of frame 1")
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
        if spec.source_mode == "runtime_master_derived":
            raise FoundryError(f"Reviewed runtime master is missing: {spec.source}.")
        raise FoundryError(f"Source image is missing: {spec.source}.")
    with Image.open(spec.source) as source_file:
        if spec.source_mode == "canonical_derived":
            cells, prepared = derive_canonical_frames(source_file, spec)
        elif spec.source_mode == "runtime_master_derived":
            cells = derive_runtime_master_frames(source_file, spec)
            prepared = None
        else:
            frames = extract_frames(source_file, spec.frames)
            cells = normalize_frames(frames, spec)
            prepared = None
    sheet = make_sheet(cells, spec)
    report = validate(cells, sheet, spec)
    if not report.passed:
        raise FoundryError("Validation failed: " + "; ".join(report.reasons))
    normalized = root / "art/generated/normalized" / f"{spec.name}.png"
    normalized.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(normalized)
    if prepared is not None:
        derived = root / "art/generated/derived" / f"{spec.name}_canonical_clean.png"
        derived.parent.mkdir(parents=True, exist_ok=True)
        prepared.save(derived)
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
