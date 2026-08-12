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

    def test_extracts_equally_slotted_opaque_reference_export(self):
        image = Image.new("RGB", (80, 40), (90, 90, 90))
        draw = ImageDraw.Draw(image)
        for index in range(4):
            left = index * 20
            draw.rectangle((left + 6, 10, left + 13, 31), fill=(30, 100, 60))
        frames = foundry.extract_frames(image, 4)
        self.assertEqual(4, len(frames))
        self.assertEqual((8, 22), frames[0].size)
        self.assertEqual((30, 100, 60, 255), frames[0].getpixel((0, 0)))

    def test_derives_breathing_frames_from_one_canonical_source(self):
        canonical = Image.new("RGB", (80, 100), (90, 90, 90))
        draw = ImageDraw.Draw(canonical)
        draw.rectangle((28, 12, 51, 79), fill=(30, 100, 60))
        draw.rectangle((34, 88, 45, 96), fill=(0, 0, 0))  # Presentation label: must be removed.
        derived_spec = foundry.SpriteSpec(
            "guardian_idle", Path("unused.png"), 4, 64, 64, 48, "bottom-center", "canonical_derived",
        )
        cells, prepared = foundry.derive_canonical_frames(canonical, derived_spec)
        expected_seed = foundry.normalize_frames([prepared], foundry.SpriteSpec(
            "guardian_idle", Path("unused.png"), 1, 64, 64, 48, "bottom-center", "canonical_derived",
        ))[0]
        self.assertEqual(4, len(cells))
        self.assertEqual(expected_seed.tobytes(), cells[0].tobytes())
        self.assertEqual(expected_seed.tobytes(), cells[2].tobytes())
        self.assertEqual((24, 68), prepared.size)
        self.assertEqual([64, 63, 64, 64], [foundry.alpha_bbox(cell)[3] for cell in cells])
        self.assertEqual(cells[1].tobytes(), foundry._translate_cell(cells[0], -1, derived_spec).tobytes())
        self.assertTrue(foundry.validate(cells, foundry.make_sheet(cells, derived_spec), derived_spec).passed)

    def test_canonical_matte_removes_light_fringe_without_removing_cream_foreground(self):
        background = (230, 220, 200)
        canonical = Image.new("RGB", (40, 40), background)
        draw = ImageDraw.Draw(canonical)
        draw.rectangle((12, 10, 27, 29), fill=(245, 205, 160))  # Cream Guardian mask/body.
        draw.rectangle((11, 10, 11, 29), fill=(236, 223, 205))  # Matte fringe.
        draw.rectangle((28, 10, 28, 29), fill=(236, 223, 205))
        prepared = foundry.prepare_canonical_source(canonical)
        self.assertEqual((16, 20), prepared.size)
        self.assertEqual((245, 205, 160, 255), prepared.getpixel((0, 0)))
        self.assertTrue(all(pixel[3] == 255 for pixel in prepared.get_flattened_data()))

    def test_runtime_master_mode_keeps_reviewed_pixels_and_requires_transparency(self):
        master = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        ImageDraw.Draw(master).rectangle((12, 4, 51, 63), fill=(30, 100, 60, 255))
        spec = foundry.SpriteSpec(
            "guardian_idle", Path("guardian_front_runtime.png"), 4, 64, 64, 60, "bottom-center",
            "runtime_master_derived", (0, -1, 0, 0), Path("guardian_canonical_front.png"),
            Path("guardian_front_runtime.png"),
        )
        cells = foundry.derive_runtime_master_frames(master, spec)
        self.assertEqual(master.tobytes(), cells[0].tobytes())
        self.assertEqual(cells[1].tobytes(), foundry._translate_cell(master, -1, spec).tobytes())
        self.assertTrue(foundry.validate(cells, foundry.make_sheet(cells, spec), spec).passed)
        with self.assertRaisesRegex(foundry.FoundryError, "transparent"):
            foundry.derive_runtime_master_frames(Image.new("RGBA", (64, 64), (1, 2, 3, 255)), spec)

    def test_manifest_distinguishes_presentation_reference_from_runtime_master(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "art/manifests").mkdir(parents=True)
            (root / "art/manifests/sprites.yaml").write_text(
                "assets:\n  guardian_idle:\n    presentation_reference: art/generated/source/guardian_canonical_front.png\n    runtime_master: art/generated/source/guardian_front_runtime.png\n    source_mode: runtime_master_derived\n    frames: 4\n    runtime_cell: [64, 64]\n    nominal_character_height: 60\n    anchor: bottom-center\n",
                encoding="utf-8",
            )
            spec = foundry.load_spec(root / "art/manifests/sprites.yaml", "guardian_idle", root)
            self.assertEqual(root / "art/generated/source/guardian_canonical_front.png", spec.presentation_reference)
            self.assertEqual(root / "art/generated/source/guardian_front_runtime.png", spec.runtime_master)
            with self.assertRaisesRegex(foundry.FoundryError, "Reviewed runtime master is missing"):
                foundry.run("guardian_idle", root)

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
