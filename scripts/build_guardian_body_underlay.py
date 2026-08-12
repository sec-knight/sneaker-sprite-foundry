#!/usr/bin/env python3
"""Create the reviewed one-row torso/cloth underlay for Guardian's body inhale gap."""

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "art/generated/source/guardian_front_runtime_approved.png"
OUT = ROOT / "art/underlays/guardian/body_gap.png"


def main() -> None:
    master = Image.open(MASTER).convert("RGBA")
    underlay = Image.new("RGBA", master.size, (0, 0, 0, 0))
    # The moved body vacates y=46.  Copy its immediately adjacent lower torso/cloth row.
    for x in range(15, 48):
        underlay.putpixel((x, 46), master.getpixel((x, 47)))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    underlay.save(OUT)
    debug = master.copy()
    added = Image.new("RGBA", master.size, (255, 70, 210, 220))
    debug.alpha_composite(Image.composite(added, Image.new("RGBA", master.size), underlay.getchannel("A")))
    debug = debug.resize((512, 512), Image.Resampling.NEAREST)
    ImageDraw.Draw(debug).rectangle((0, 0, 511, 511), outline=(255, 255, 255, 130), width=1)
    preview = ROOT / "art/generated/review/guardian_idle_body_underlay_preview.png"
    preview.parent.mkdir(parents=True, exist_ok=True)
    debug.save(preview)
    print(preview)


if __name__ == "__main__":
    main()
