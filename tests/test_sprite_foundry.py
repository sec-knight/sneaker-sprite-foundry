import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from PIL import Image, ImageDraw
import sprite_foundry as foundry


class SpriteFoundryTests(unittest.TestCase):
    def setUp(self):
        self.spec = foundry.SpriteSpec("guardian_idle", Path("unused.png"), 4, 64, 64, 48, "bottom-center")

    def source_strip(self):
        image = Image.new("RGBA", (180, 80), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        for index, left in enumerate((5, 50, 95, 140)):
            draw.rectangle((left, 10 + index % 2, left + 25, 70 + index % 2), fill=(30, 100, 60, 255))
        return image

    def test_extracts_four_transparent_separated_frames(self):
        frames = foundry.extract_frames(self.source_strip(), 4)
        self.assertEqual(4, len(frames))
        self.assertEqual((26, 61), frames[0].size)

    def test_rejects_wrong_source_frame_count(self):
        with self.assertRaisesRegex(foundry.FoundryError, "Expected 5"):
            foundry.extract_frames(self.source_strip(), 5)

    def test_shared_scale_and_bottom_center_placement(self):
        cells = foundry.normalize_frames(foundry.extract_frames(self.source_strip(), 4), self.spec)
        boxes = [foundry.alpha_bbox(cell) for cell in cells]
        self.assertTrue(all(box[3] == 64 for box in boxes))
        self.assertEqual(48, max(box[3] - box[1] for box in boxes))
        self.assertLessEqual(max((box[2] - box[0]) for box in boxes) - min((box[2] - box[0]) for box in boxes), 1)

    def test_sheet_dimensions_and_alpha(self):
        cells = foundry.normalize_frames(foundry.extract_frames(self.source_strip(), 4), self.spec)
        sheet = foundry.make_sheet(cells, self.spec)
        self.assertEqual((256, 64), sheet.size)
        self.assertEqual(0, sheet.getchannel("A").getextrema()[0])
        self.assertTrue(foundry.validate(cells, sheet, self.spec).passed)

    def test_validation_reports_unanchored_cell(self):
        cell = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        ImageDraw.Draw(cell).rectangle((20, 10, 40, 50), fill=(1, 2, 3, 255))
        report = foundry.validate([cell] * 4, foundry.make_sheet([cell] * 4, self.spec), self.spec)
        self.assertFalse(report.passed)
        self.assertIn("not bottom anchored", " ".join(report.reasons))

    def test_manifest_parsing_and_missing_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "art/manifests").mkdir(parents=True)
            (root / "art/manifests/sprites.yaml").write_text(
                "assets:\n  guardian_idle:\n    source: art/generated/source/guardian_idle.png\n    frames: 4\n    runtime_cell: [64, 64]\n    nominal_character_height: 48\n    anchor: bottom-center\n",
                encoding="utf-8",
            )
            spec = foundry.load_spec(root / "art/manifests/sprites.yaml", "guardian_idle", root)
            self.assertEqual(root / "art/generated/source/guardian_idle.png", spec.source)
            with self.assertRaisesRegex(foundry.FoundryError, "Source image is missing"):
                foundry.run("guardian_idle", root)

    def test_end_to_end_writes_runtime_sheet_and_previews(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "art/manifests").mkdir(parents=True)
            (root / "art/generated/source").mkdir(parents=True)
            (root / "art/manifests/sprites.yaml").write_text(
                "assets:\n  guardian_idle:\n    source: art/generated/source/guardian_idle.png\n    frames: 4\n    runtime_cell: [64, 64]\n    nominal_character_height: 48\n    anchor: bottom-center\n",
                encoding="utf-8",
            )
            self.source_strip().save(root / "art/generated/source/guardian_idle.png")
            self.assertTrue(foundry.run("guardian_idle", root).passed)
            with Image.open(root / "art/generated/normalized/guardian_idle.png") as sheet:
                self.assertEqual((256, 64), sheet.size)
            self.assertTrue((root / "art/generated/previews/guardian_idle_preview.png").exists())
            self.assertTrue((root / "art/generated/previews/guardian_idle.gif").exists())


if __name__ == "__main__":
    unittest.main()
