# Sneaker Sprite Foundry

A tiny production pipeline for turning approved Sneaker Games character concepts into clean, validated runtime sprite assets.

## First proof

The first and only active work item is `guardian_idle`.

Goal: take one source image containing four front-facing Plushy Guardian idle poses and deterministically produce:

- a 4-frame runtime sprite sheet
- 64×64 cells
- ~48 px nominal character height
- bottom-center alignment
- transparency
- a human-reviewable preview
- explicit validation results

## Repository shape

```text
art/
  bible/
  references/
  manifests/
  generated/
    source/
    normalized/
    previews/
scripts/
tests/
```

## Production philosophy

The image model creates coherent source art. The foundry handles boring precision: crop, scale, alignment, export, validation, preview.

Do not silently repair design drift. If the Guardian suddenly mutates, surface it for human review.

## Scope guardrail

Do not add walk, cast, roll, enemies, title-screen assets, or game integration until `guardian_idle` has succeeded end to end.
