import tempfile
import unittest
import hashlib
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from PIL import Image, ImageDraw
import yaml
import sprite_foundry as foundry
import prepare_runtime_candidate as candidate_prep
import prepare_wisp_size_comparison as wisp_comparison


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

    def test_region_derived_frames_move_only_masked_pixels_without_new_colours(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            master_path = root / "master.png"
            master = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(master)
            draw.rectangle((20, 10, 43, 43), fill=(30, 100, 60, 255))
            draw.rectangle((20, 44, 43, 63), fill=(120, 70, 30, 255))
            master.save(master_path)
            body_mask = Image.new("L", (64, 64))
            ImageDraw.Draw(body_mask).rectangle((20, 10, 43, 43), fill=255)
            mask_path = root / "body.png"
            body_mask.save(mask_path)
            spec = foundry.SpriteSpec(
                "guardian_idle", master_path, 4, 64, 64, 60, "bottom-center",
                "runtime_master_region_derived", (), None, None, master_path,
                hashlib.sha256(master_path.read_bytes()).hexdigest(), (("body", mask_path),),
                ((), (("body", (0, -1)),), (), ()),
            )
            cells, warnings = foundry.derive_runtime_master_region_frames(master, spec, master_path)
            self.assertFalse(warnings)
            self.assertEqual(master.tobytes(), cells[0].tobytes())
            self.assertEqual((30, 100, 60, 255), cells[1].getpixel((20, 9)))
            self.assertEqual((0, 0, 0, 0), cells[1].getpixel((20, 43)))
            self.assertEqual((120, 70, 30, 255), cells[1].getpixel((20, 44)))
            self.assertTrue(set(cells[1].get_flattened_data()) <= set(master.get_flattened_data()))

    def test_region_derived_rejects_checksum_or_nonbinary_mask(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            master_path = root / "master.png"
            master = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            ImageDraw.Draw(master).rectangle((20, 10, 43, 63), fill=(30, 100, 60, 255))
            master.save(master_path)
            mask_path = root / "mask.png"
            Image.new("L", (64, 64), 127).save(mask_path)
            spec = foundry.SpriteSpec(
                "guardian_idle", master_path, 4, 64, 64, 60, "bottom-center",
                "runtime_master_region_derived", (), None, None, master_path, "not-a-hash",
                (("body", mask_path),), ((), (), (), ()),
            )
            with self.assertRaisesRegex(foundry.FoundryError, "checksum"):
                foundry.derive_runtime_master_region_frames(master, spec, master_path)
            spec = foundry.SpriteSpec(
                "guardian_idle", master_path, 4, 64, 64, 60, "bottom-center",
                "runtime_master_region_derived", (), None, None, master_path,
                hashlib.sha256(master_path.read_bytes()).hexdigest(), (("body", mask_path),), ((), (), (), ()),
            )
            with self.assertRaisesRegex(foundry.FoundryError, "binary"):
                foundry.derive_runtime_master_region_frames(master, spec, master_path)

    def test_region_underlay_is_hidden_in_neutral_and_only_fills_body_vacancy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            master_path = root / "master.png"
            master = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(master)
            draw.rectangle((20, 10, 43, 43), fill=(30, 100, 60, 255))
            draw.rectangle((20, 44, 43, 63), fill=(120, 70, 30, 255))
            master.save(master_path)
            mask = Image.new("L", (64, 64))
            ImageDraw.Draw(mask).rectangle((20, 10, 43, 43), fill=255)
            mask_path = root / "body.png"
            mask.save(mask_path)
            underlay = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            for x in range(20, 44):
                underlay.putpixel((x, 43), master.getpixel((x, 44)))
            underlay_path = root / "underlay.png"
            underlay.save(underlay_path)
            spec = foundry.SpriteSpec(
                "guardian_idle", master_path, 4, 64, 64, 60, "bottom-center",
                "runtime_master_region_derived", (), None, None, master_path,
                hashlib.sha256(master_path.read_bytes()).hexdigest(), (("body", mask_path),),
                ((), (("body", (0, -1)),), (), ()), (("body_gap", underlay_path, ("body",)),),
            )
            cells, _ = foundry.derive_runtime_master_region_frames(master, spec, master_path)
            self.assertEqual(master.tobytes(), cells[0].tobytes())
            self.assertEqual((120, 70, 30, 255), cells[1].getpixel((20, 43)))
            self.assertEqual((30, 100, 60, 255), cells[1].getpixel((20, 9)))
            bad = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            bad.putpixel((1, 1), (255, 0, 255, 255))
            bad.save(underlay_path)
            with self.assertRaisesRegex(foundry.FoundryError, "not present"):
                foundry.derive_runtime_master_region_frames(master, spec, master_path)

    def test_runtime_candidate_preparation_is_review_only_and_bottom_centered(self):
        candidate = Image.new("RGBA", (160, 240), (4, 3, 1, 0))
        draw = ImageDraw.Draw(candidate)
        draw.rectangle((45, 20, 114, 219), fill=(30, 100, 60, 253))
        cell, report = candidate_prep.prepare_runtime_candidate(candidate, (64, 64), 60)
        self.assertEqual((64, 64), cell.size)
        self.assertEqual((21, 4, 42, 64), foundry.alpha_bbox(cell))
        self.assertEqual(60, report.visual_height)
        self.assertEqual((0, 255), report.output_alpha_extrema)
        self.assertTrue(report.touches_cell_boundary)  # Bottom anchor is expected to touch.

    def test_runtime_candidate_review_output_cannot_be_approved_master_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "art/manifests").mkdir(parents=True)
            (root / "art/generated/source").mkdir(parents=True)
            candidate = root / "art/generated/source/guardian_front_runtime.png"
            Image.new("RGBA", (64, 64), (1, 2, 3, 253)).save(candidate)
            (root / "art/manifests/sprites.yaml").write_text(
                "assets:\n  guardian_idle:\n    runtime_candidate: art/generated/source/guardian_front_runtime.png\n    runtime_master: art/generated/review/guardian_runtime.png\n    source_mode: runtime_master_derived\n    frames: 4\n    runtime_cell: [64, 64]\n    nominal_character_height: 60\n    anchor: bottom-center\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(foundry.FoundryError, "must never equal"):
                candidate_prep.run("guardian_idle", root)

    def test_wisp_metrics_report_geometry_and_diagnostic_readability(self):
        source = Image.new("RGBA", (80, 100), (0, 0, 0, 0))
        draw = ImageDraw.Draw(source)
        draw.polygon(((30, 0), (48, 32), (42, 75), (35, 99), (28, 70), (24, 35)), fill=(40, 180, 50, 255))
        draw.ellipse((27, 45, 45, 73), fill=(255, 245, 205, 255))
        draw.ellipse((31, 54, 33, 59), fill=(25, 20, 15, 255))
        draw.ellipse((39, 54, 41, 59), fill=(25, 20, 15, 255))
        draw.polygon(((23, 50), (4, 42), (20, 64)), fill=(45, 170, 55, 255))
        draw.polygon(((48, 50), (68, 42), (52, 64)), fill=(45, 170, 55, 255))
        with tempfile.TemporaryDirectory() as directory:
            metrics = wisp_comparison.prepare_one(source, 20, (24, 24), Path(directory))
            self.assertEqual((24, 24), metrics.runtime_cell)
            self.assertEqual(20, metrics.visual_height)
            self.assertGreater(metrics.occupancy_percent, 0)
            self.assertTrue(Path(metrics.output).exists())
            self.assertTrue(Path(metrics.preview).exists())

    def test_approved_wisp_master_is_the_approved_review_candidate_byte_for_byte(self):
        root = Path(__file__).resolve().parents[1]
        candidate = root / "art/generated/review/wisp/wisp_20px_review.png"
        master = root / "art/generated/source/wisp_front_runtime_approved.png"
        checksum = (root / "art/generated/source/wisp_front_runtime_approved.sha256").read_text(encoding="utf-8").split()[0]
        self.assertEqual(candidate.read_bytes(), master.read_bytes())
        self.assertEqual("c1c7332fd2344ef689e928ff35d325658af25082fe3a33d85da2087401ffd422", checksum)
        self.assertEqual(checksum, hashlib.sha256(master.read_bytes()).hexdigest())

    def test_wisp_spec_records_identity_runtime_authorities_and_awaiting_hover(self):
        root = Path(__file__).resolve().parents[1]
        manifest = yaml.safe_load((root / "art/manifests/sprites.yaml").read_text(encoding="utf-8"))
        wisp = manifest["assets"]["forest_wisp"]
        self.assertEqual("art/references/wisp/wisp_front_canonical.png", wisp["presentation_reference"])
        self.assertEqual("art/generated/source/wisp_front_runtime_approved.png", wisp["runtime_master"])
        self.assertEqual([24, 24], wisp["runtime_cell"])
        self.assertEqual(20, wisp["nominal_character_height"])
        hover = manifest["assets"]["wisp_hover"]
        self.assertEqual("runtime_frame_strip", hover["source_mode"])
        self.assertEqual("awaiting_reviewed_runtime_frames", hover["status"])
        self.assertEqual(wisp["runtime_master"], hover["runtime_frames"][0])
        self.assertEqual(wisp["runtime_master"], hover["runtime_frames"][2])

    def test_runtime_frame_strip_packs_reviewed_cell_pixels_without_transformation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            master_path = root / "master.png"
            stretch_path = root / "stretch.png"
            tilt_path = root / "tilt.png"
            master = Image.new("RGBA", (24, 24), (0, 0, 0, 0))
            ImageDraw.Draw(master).ellipse((7, 5, 16, 20), fill=(240, 255, 190, 173))
            stretch = master.copy()
            stretch.putpixel((11, 2), (40, 190, 70, 255))
            tilt = master.copy()
            tilt.putpixel((18, 14), (55, 200, 80, 255))
            master.save(master_path)
            stretch.save(stretch_path)
            tilt.save(tilt_path)
            checksum = hashlib.sha256(master_path.read_bytes()).hexdigest()
            spec = foundry.SpriteSpec(
                "wisp_hover", master_path, 4, 24, 24, 20, "bottom-center",
                "runtime_frame_strip", runtime_master=master_path, runtime_master_sha256=checksum,
                runtime_frame_sources=(master_path, stretch_path, master_path, tilt_path),
            )
            cells = foundry.derive_runtime_frame_strip_frames(spec, master_path)
            sheet = foundry.make_runtime_frame_strip_sheet(cells, spec)
            self.assertEqual((96, 24), sheet.size)
            for index, path in enumerate(spec.runtime_frame_sources):
                with Image.open(path) as source:
                    expected = source.convert("RGBA").tobytes()
                self.assertEqual(expected, sheet.crop((index * 24, 0, (index + 1) * 24, 24)).tobytes())
            self.assertEqual(master_path.read_bytes(), spec.runtime_frame_sources[0].read_bytes())
            self.assertEqual(master_path.read_bytes(), spec.runtime_frame_sources[2].read_bytes())

    def test_runtime_frame_strip_rejects_wrong_dimensions_and_changed_neutral(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            master_path = root / "master.png"
            variant_path = root / "variant.png"
            bad_path = root / "bad.png"
            Image.new("RGBA", (24, 24), (20, 180, 50, 255)).save(master_path)
            Image.new("RGBA", (24, 24), (40, 190, 70, 255)).save(variant_path)
            Image.new("RGBA", (23, 24), (40, 190, 70, 255)).save(bad_path)
            checksum = hashlib.sha256(master_path.read_bytes()).hexdigest()
            spec = foundry.SpriteSpec(
                "wisp_hover", master_path, 4, 24, 24, 20, "bottom-center",
                "runtime_frame_strip", runtime_master=master_path, runtime_master_sha256=checksum,
                runtime_frame_sources=(master_path, bad_path, master_path, variant_path),
            )
            with self.assertRaisesRegex(foundry.FoundryError, "Runtime frame 2 is"):
                foundry.derive_runtime_frame_strip_frames(spec, master_path)
            spec = foundry.SpriteSpec(
                "wisp_hover", master_path, 4, 24, 24, 20, "bottom-center",
                "runtime_frame_strip", runtime_master=master_path, runtime_master_sha256=checksum,
                runtime_frame_sources=(variant_path, variant_path, master_path, variant_path),
            )
            with self.assertRaisesRegex(foundry.FoundryError, "Frames 1 and 3"):
                foundry.derive_runtime_frame_strip_frames(spec, master_path)

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
