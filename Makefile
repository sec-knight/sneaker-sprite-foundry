.PHONY: sprite test

ASSET ?= guardian_idle

sprite:
	python scripts/sprite_foundry.py $(ASSET)

test:
	python -m unittest discover -s tests -v
