import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import mediasorter_core as core


class CoreUtilsTests(unittest.TestCase):
    def test_none_provider_is_always_available(self):
        self.assertTrue(core.is_ai_provider_installed(core.AI_PROVIDER_NONE))

    def test_ai_model_options_exist_for_clip_provider(self):
        opts = core.get_ai_model_options(provider_id=core.AI_PROVIDER_CLIP_LOCAL)
        self.assertGreaterEqual(len(opts), 3)
        ids = {str(o.get("id")) for o in opts}
        self.assertIn("clip_vit_b32_openai", ids)

    def test_set_ai_model_profile_updates_selected_model(self):
        old = core.get_ai_model_id()
        try:
            core.set_ai_model_profile("clip_vit_b16_openai")
            self.assertEqual(core.get_ai_model_id(), "clip_vit_b16_openai")
        finally:
            core.set_ai_model_profile(old)

    def test_heic_support_status_shape(self):
        status = core.get_heic_support_status()
        self.assertIn("supported", status)
        self.assertIn("detail", status)
        self.assertIn("backend", status)
        self.assertIsInstance(status["supported"], bool)

    def test_heic_status_includes_import_error_details(self):
        old_has = core.HAS_PILLOW_HEIF
        old_err = core._PILLOW_HEIF_IMPORT_ERROR
        try:
            core.HAS_PILLOW_HEIF = False
            core._PILLOW_HEIF_IMPORT_ERROR = "ImportError: DLL load failed while importing _pillow_heif"
            with (
                patch.object(core, "_init_heic_support", lambda *args, **kwargs: None),
                patch.object(core.Image, "registered_extensions", return_value={}),
                patch.object(core.Image, "OPEN", {}),
            ):
                status = core.get_heic_support_status()
            self.assertIn("DLL load failed", status["detail"])
            self.assertIn("Native HEIF library loading failed", status["detail"])
            self.assertEqual(status["import_error"], core._PILLOW_HEIF_IMPORT_ERROR)
        finally:
            core.HAS_PILLOW_HEIF = old_has
            core._PILLOW_HEIF_IMPORT_ERROR = old_err

    def test_safe_folder_name_sanitizes_invalid_characters(self):
        self.assertEqual(core._safe_folder_name("  inv<al>:*?id\\. "), "inv_al____id_")
        self.assertEqual(core._safe_folder_name("   "), "Unknown")

    def test_unique_dest_path_adds_increment_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "photo.jpg").write_bytes(b"")
            (tmp_path / "photo_1.jpg").write_bytes(b"")
            candidate = core._unique_dest_path(tmp, "photo.jpg")
            self.assertEqual(Path(candidate).name, "photo_2.jpg")

    def test_render_structure_appends_category_when_missing(self):
        tokens = {
            "category": "family/photo",
            "year": "2026",
            "month": "03",
            "yearmonth": "2026-03",
            "yearmo": "202603",
            "location": None,
        }
        out = core._render_structure("OUT", "{year}/{location}", tokens)
        self.assertEqual(out, os.path.join("OUT", "2026", "family_photo"))

    def test_render_structure_uses_category_when_pattern_is_empty(self):
        tokens = {
            "category": "pets",
            "year": None,
            "month": None,
            "yearmonth": None,
            "yearmo": None,
            "location": None,
        }
        out = core._render_structure("OUT", "", tokens)
        self.assertEqual(out, os.path.join("OUT", "pets"))

    def test_noise_reduction_prefers_specific_when_generic_is_close(self):
        cat, score = core._reduce_generic_prediction_noise(
            "family photo",
            0.500,
            [("family photo", 0.500), ("pet", 0.492)],
        )
        self.assertEqual(cat, "pet")
        self.assertAlmostEqual(score, 0.492, places=3)

    def test_noise_reduction_keeps_generic_when_gap_is_large(self):
        cat, score = core._reduce_generic_prediction_noise(
            "family photo",
            0.500,
            [("family photo", 0.500), ("pet", 0.450)],
        )
        self.assertEqual(cat, "family photo")
        self.assertAlmostEqual(score, 0.500, places=3)

    def test_noise_reduction_stabilizes_selfie_to_family_photo(self):
        cat, score = core._reduce_generic_prediction_noise(
            "selfie",
            0.430,
            [("selfie", 0.430), ("family photo", 0.424), ("pet", 0.200)],
        )
        self.assertEqual(cat, "family photo")
        self.assertAlmostEqual(score, 0.424, places=3)


if __name__ == "__main__":
    unittest.main()
