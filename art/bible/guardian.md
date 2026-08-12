# Plushy Guardian — Canonical Art Direction

## Role

The Plushy Guardian is the central character of the Tiny Forest prototype and a recurring Sneaker Games mascot character.

## Canonical presentation

- chibi plush forest guardian
- nominal runtime height: ~48 px
- runtime cell: 64×64 px
- bottom-center anchor
- front/back/side movement language; side may be mirrored in code
- calm, cozy, slightly sleepy personality
- readable mask/face at phone scale
- branch antlers decorated with leaves and small flowers
- earthy cream, brown, moss green, rose-pink, and muted gold palette
- cloak and emerald brooch are important identity features
- staff is part of the design, but does not need to indicate exact aim direction

## UI / targeting language

Exact targeting direction is represented by a separate green wisp outside the character. The wisp can later serve as targeting reticle, menu focus marker, mouse cursor, interaction hint, and other diegetic UI roles.

## Guardian idle — first production asset

The first production target is `guardian_idle`:

- 4 frames
- front-facing
- subtle breathing/bobbing
- tiny natural movement in leaves, antlers, cloak, or staff
- calm neutral expression
- seamless loop feel
- no exaggerated pose changes
- transparent source/background

## Production rule

Generated source art is allowed to be large. Precision belongs to the deterministic foundry step: crop, shared scale, anchor, 64×64 cells, transparency validation, runtime export, and preview generation.

Visual design drift must never be silently accepted. A malformed Guardian should fail human review rather than be mechanically "fixed" into production.
