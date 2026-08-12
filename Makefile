.PHONY: sprite build-guardian-masks build-guardian-underlay prepare-runtime-master test

ASSET ?= guardian_idle

sprite:
	python scripts/sprite_foundry.py $(ASSET)

build-guardian-masks:
	python scripts/build_guardian_region_masks.py

build-guardian-underlay:
	python scripts/build_guardian_body_underlay.py

prepare-runtime-master:
	python scripts/prepare_runtime_candidate.py $(ASSET)

test:
	python -m unittest discover -s tests -v
