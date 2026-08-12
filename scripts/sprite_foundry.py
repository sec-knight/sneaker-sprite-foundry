#!/usr/bin/env python3
"""Deterministic normalizer for the Sneaker Sprite Foundry vertical slice."""

from __future__ import annotations

import argparse
import hashlib
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
    runtime_candidate: Path | None = None
    runtime_master: Path | None = None
    runtime_master_sha256: str | None = None
    region_masks: tuple[tuple[str, Path], ...] = ()
    region_frames: tuple[tuple[tuple[str, tuple[int, int]], ...], ...] = ()
    underlays: tuple[tuple[str, Path, tuple[str, ...]], ...] = ()
    runtime_frame_sources: tuple[Path, ...] = ()


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
    if source_mode not in {"strip", "canonical_derived", "runtime_master_derived", "runtime_master_region_derived", "runtime_frame_strip"}:
        raise FoundryError(f"Unsupported source mode: {source_mode}.")
    runtime_master = root / asset["runtime_master"] if asset.get("runtime_master") else None
    runtime_candidate = root / asset["runtime_candidate"] if asset.get("runtime_candidate") else None
    presentation_reference = root / asset["presentation_reference"] if asset.get("presentation_reference") else None
    if source_mode in {"runtime_master_derived", "runtime_master_region_derived", "runtime_frame_strip"} and runtime_master is None:
        raise FoundryError(f"{source_mode} assets must declare runtime_master.")
    runtime_frame_sources = tuple(root / path for path in asset.get("runtime_frames", ()))
    if source_mode == "runtime_frame_strip" and len(runtime_frame_sources) != int(asset["frames"]):
        raise FoundryError("runtime_frame_strip assets must declare one runtime_frames path per frame.")
    motion = asset.get("motion", {})
    regions = tuple((name, root / path) for name, path in motion.get("regions", {}).items())
    region_frames = tuple(tuple((name, tuple(offset)) for name, offset in frame.items()) for frame in motion.get("frames", ()))
    underlays = tuple((name, root / underlay["source"], tuple(underlay.get("expose_when", ())) )
                      for name, underlay in motion.get("underlays", {}).items())
    return SpriteSpec(
        name=asset_name,
        source=runtime_master if runtime_master is not None else root / asset["source"],
        frames=int(asset["frames"]),
        cell_width=int(cell_width),
        cell_height=int(cell_height),
        nominal_height=int(asset["nominal_character_height"]),
        anchor=asset["anchor"],
        source_mode=source_mode,
        motion_offsets=tuple(int(offset) for offset in motion.get("offsets_y", (0,) * int(asset["frames"]))),
        presentation_reference=presentation_reference,
        runtime_candidate=runtime_candidate,
        runtime_master=runtime_master,
        runtime_master_sha256=asset.get("runtime_master_sha256"),
        region_masks=regions,
        region_frames=region_frames,
        underlays=underlays,
        runtime_frame_sources=runtime_frame_sources,
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


def derive_runtime_frame_strip_frames(spec: SpriteSpec, master_path: Path) -> list[Image.Image]:
    """Load reviewed runtime cells exactly as authored, without any pixel transformation."""
    if len(spec.runtime_frame_sources) != spec.frames:
        raise FoundryError("runtime_frame_strip must provide exactly one source path per declared frame.")
    if spec.runtime_master_sha256 is None:
        raise FoundryError("runtime_frame_strip assets must declare runtime_master_sha256.")
    if hashlib.sha256(master_path.read_bytes()).hexdigest() != spec.runtime_master_sha256.lower():
        raise FoundryError("Approved runtime-master checksum does not match the manifest.")
    master_bytes = master_path.read_bytes()
    cells: list[Image.Image] = []
    source_pixel_bytes: list[bytes] = []
    for index, path in enumerate(spec.runtime_frame_sources, start=1):
        if not path.exists():
            raise FoundryError(f"Runtime frame {index} is missing: {path}.")
        with Image.open(path) as source:
            if source.size != (spec.cell_width, spec.cell_height):
                raise FoundryError(
                    f"Runtime frame {index} is {source.size}, expected {(spec.cell_width, spec.cell_height)}."
                )
            cell = source.convert("RGBA")
        cells.append(cell)
        source_pixel_bytes.append(cell.tobytes())
    if spec.runtime_frame_sources[0].read_bytes() != master_bytes or spec.runtime_frame_sources[2].read_bytes() != master_bytes:
        raise FoundryError("Frames 1 and 3 of runtime_frame_strip must be byte-identical to the approved runtime master.")
    if source_pixel_bytes[0] != source_pixel_bytes[2]:
        raise FoundryError("Frames 1 and 3 of runtime_frame_strip do not preserve identical runtime pixels.")
    return cells


def _load_binary_mask(path: Path, spec: SpriteSpec, master: Image.Image) -> Image.Image:
    if not path.exists():
        raise FoundryError(f"Region mask is missing: {path}.")
    with Image.open(path) as source:
        mask = source.convert("L")
    if mask.size != (spec.cell_width, spec.cell_height):
        raise FoundryError(f"Region mask {path} is {mask.size}, expected {(spec.cell_width, spec.cell_height)}.")
    values = set(mask.get_flattened_data())
    if not values <= {0, 255}:
        raise FoundryError(f"Region mask {path} must be binary (0 or 255).")
    if mask.getbbox() is None:
        raise FoundryError(f"Region mask {path} is empty.")
    master_alpha = master.getchannel("A")
    if any(value and alpha == 0 for value, alpha in zip(mask.get_flattened_data(), master_alpha.get_flattened_data(), strict=True)):
        raise FoundryError(f"Region mask {path} selects transparent master pixels.")
    return mask


def _clear_mask(image: Image.Image, mask: Image.Image) -> None:
    data = bytearray(image.convert("RGBA").tobytes())
    transparent = next((tuple(data[index:index + 4]) for index in range(0, len(data), 4) if data[index + 3] == 0),
                       (0, 0, 0, 0))
    for index, selected in enumerate(mask.get_flattened_data()):
        if selected:
            start = index * 4
            data[start:start + 4] = bytes(transparent)
    image.paste(Image.frombytes("RGBA", image.size, bytes(data)))


def _blit_masked_region(destination: Image.Image, master: Image.Image, mask: Image.Image,
                         offset_x: int, offset_y: int) -> None:
    """Copy selected master RGBA pixels directly, with no alpha blending or colour changes."""
    source_data = master.convert("RGBA").tobytes()
    destination_data = bytearray(destination.convert("RGBA").tobytes())
    for index, selected in enumerate(mask.get_flattened_data()):
        if not selected:
            continue
        x, y = index % destination.width, index // destination.width
        target_x, target_y = x + offset_x, y + offset_y
        if 0 <= target_x < destination.width and 0 <= target_y < destination.height:
            source_start = index * 4
            target_start = (target_y * destination.width + target_x) * 4
            destination_data[target_start:target_start + 4] = source_data[source_start:source_start + 4]
    destination.paste(Image.frombytes("RGBA", destination.size, bytes(destination_data)))


def _load_underlay(path: Path, spec: SpriteSpec, master: Image.Image) -> Image.Image:
    if not path.exists():
        raise FoundryError(f"Underlay is missing: {path}.")
    with Image.open(path) as source:
        underlay = source.convert("RGBA")
    if underlay.size != (spec.cell_width, spec.cell_height):
        raise FoundryError(f"Underlay {path} is {underlay.size}, expected {(spec.cell_width, spec.cell_height)}.")
    master_pixels = set(master.get_flattened_data())
    if any(pixel not in master_pixels for pixel in underlay.get_flattened_data() if pixel[3]):
        raise FoundryError(f"Underlay {path} contains pixels not present in the approved runtime master.")
    return underlay


def _mask_from_alpha(image: Image.Image) -> Image.Image:
    return image.getchannel("A").point(lambda value: 255 if value else 0)


def _inverse_union_mask(masks: list[Image.Image], size: tuple[int, int]) -> Image.Image:
    data = bytearray([255] * (size[0] * size[1]))
    for mask in masks:
        for index, selected in enumerate(mask.get_flattened_data()):
            if selected:
                data[index] = 0
    return Image.frombytes("L", size, bytes(data))


def derive_runtime_master_region_frames(source: Image.Image, spec: SpriteSpec, master_path: Path) -> tuple[list[Image.Image], tuple[str, ...]]:
    """Move reviewed master regions by integer pixels; compositing follows manifest region order."""
    if source.size != (spec.cell_width, spec.cell_height):
        raise FoundryError(f"Runtime master must already be {(spec.cell_width, spec.cell_height)}, got {source.size}.")
    if spec.runtime_master_sha256 is None:
        raise FoundryError("runtime_master_region_derived assets must declare runtime_master_sha256.")
    actual_checksum = hashlib.sha256(master_path.read_bytes()).hexdigest()
    if actual_checksum != spec.runtime_master_sha256.lower():
        raise FoundryError("Approved runtime-master checksum does not match the manifest.")
    master = source.convert("RGBA")
    if master.getchannel("A").getextrema()[0] != 0:
        raise FoundryError("Runtime master must include real transparent background pixels.")
    if len(spec.region_frames) != spec.frames:
        raise FoundryError("Region animation must declare one transform map per frame.")
    masks = {name: _load_binary_mask(path, spec, master) for name, path in spec.region_masks}
    if not masks:
        raise FoundryError("Region animation must declare at least one mask.")
    warnings: list[str] = []
    underlays = {name: (_load_underlay(path, spec, master), expose_when)
                 for name, path, expose_when in spec.underlays}
    for left_name, left_mask in masks.items():
        for right_name, right_mask in masks.items():
            if left_name < right_name and any(a and b for a, b in zip(left_mask.get_flattened_data(), right_mask.get_flattened_data(), strict=True)):
                warnings.append(f"masks '{left_name}' and '{right_name}' overlap; manifest order wins when both move")
    frames: list[Image.Image] = []
    for frame_index, transforms in enumerate(spec.region_frames, start=1):
        if frame_index == 1 and transforms:
            raise FoundryError("Frame 1 of a region animation must be neutral.")
        selected: list[tuple[str, tuple[int, int]]] = []
        for name, offset in transforms:
            if name not in masks:
                raise FoundryError(f"Frame {frame_index} references unknown region '{name}'.")
            if len(offset) != 2 or not all(isinstance(value, int) for value in offset):
                raise FoundryError("Region transforms must be [integer_x, integer_y].")
            if offset != (0, 0):
                selected.append((name, offset))
        selected_names = {name for name, _ in selected}
        selected_masks = [masks[name] for name in selected_names]
        frame = Image.new("RGBA", master.size, (0, 0, 0, 0))
        for underlay, expose_when in underlays.values():
            if set(expose_when) <= selected_names:
                underlay_mask = _mask_from_alpha(underlay)
                if not expose_when or any(region not in masks for region in expose_when):
                    raise FoundryError("Underlay must name one or more declared triggering regions.")
                triggering_masks = [masks[region] for region in expose_when]
                if any(value and not any(mask.get_flattened_data()[index] for mask in triggering_masks)
                       for index, value in enumerate(underlay_mask.get_flattened_data())):
                    raise FoundryError("Underlay pixels may only occupy source pixels vacated by their triggering region.")
                _blit_masked_region(frame, underlay, underlay_mask, 0, 0)
        _blit_masked_region(frame, master, _inverse_union_mask(selected_masks, master.size), 0, 0)
        for name, (offset_x, offset_y) in selected:
            _blit_masked_region(frame, master, masks[name], offset_x, offset_y)
        frames.append(frame)
    return frames, tuple(warnings)


def make_sheet(cells: list[Image.Image], spec: SpriteSpec) -> Image.Image:
    sheet = Image.new("RGBA", (spec.cell_width * spec.frames, spec.cell_height), (0, 0, 0, 0))
    for index, cell in enumerate(cells):
        sheet.alpha_composite(cell, (index * spec.cell_width, 0))
    return sheet


def make_runtime_frame_strip_sheet(cells: list[Image.Image], spec: SpriteSpec) -> Image.Image:
    """Pack reviewed cells by direct pixel copy; alpha compositing is intentionally forbidden."""
    if len(cells) != spec.frames:
        raise FoundryError(f"Expected {spec.frames} runtime cells, received {len(cells)}.")
    sheet = Image.new("RGBA", (spec.cell_width * spec.frames, spec.cell_height), (0, 0, 0, 0))
    for index, cell in enumerate(cells):
        if cell.size != (spec.cell_width, spec.cell_height):
            raise FoundryError(f"Runtime frame {index + 1} has wrong dimensions.")
        sheet.paste(cell, (index * spec.cell_width, 0))
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
        elif spec.source_mode == "runtime_master_region_derived":
            cells, warnings = derive_runtime_master_region_frames(source_file, spec, spec.source)
            prepared = None
        elif spec.source_mode == "runtime_frame_strip":
            cells = derive_runtime_frame_strip_frames(spec, spec.source)
            prepared = None
        else:
            frames = extract_frames(source_file, spec.frames)
            cells = normalize_frames(frames, spec)
            prepared = None
    sheet = make_runtime_frame_strip_sheet(cells, spec) if spec.source_mode == "runtime_frame_strip" else make_sheet(cells, spec)
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
    if spec.source_mode == "runtime_master_region_derived" and warnings:
        print("WARNING: " + "; ".join(warnings))
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
