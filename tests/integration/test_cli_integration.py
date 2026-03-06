import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import mediasorter_cli as cli
import mediasorter_core as core


class CliIntegrationTests(unittest.TestCase):
    def test_heic_status_prints_json(self):
        payload = {
            "supported": True,
            "backend": "pillow-heif",
            "heic_decoder": True,
            "heif_decoder": True,
            "detail": "ok",
            "import_error": None,
        }
        with (
            patch.object(core, "get_heic_support_status", return_value=payload),
            patch("builtins.print") as mocked_print,
        ):
            rc = cli.main(["--heic-status"])

        self.assertEqual(rc, 0)
        mocked_print.assert_called_once()
        emitted = json.loads(mocked_print.call_args[0][0])
        self.assertTrue(emitted["supported"])
        self.assertEqual(emitted["backend"], "pillow-heif")

    def test_classify_dir_json_outputs_predictions_and_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            in_dir = Path(tmp) / "input"
            in_dir.mkdir(parents=True, exist_ok=True)
            (in_dir / "a.jpg").write_bytes(b"fake-image-a")
            (in_dir / "b.jpg").write_bytes(b"fake-image-b")

            with (
                patch.object(core, "_ensure_model_loaded") as ensure_model_loaded,
                patch.object(core, "load_image_for_ai", side_effect=[object(), None]),
                patch.object(core, "_rank_categories_from_pil", return_value=[("family photo", 0.91), ("pet", 0.44)]),
                patch("builtins.print") as mocked_print,
            ):
                rc = cli.main(["--classify-dir", str(in_dir), "--topk", "2", "--json"])

            self.assertEqual(rc, 0)
            ensure_model_loaded.assert_called_once()

            outputs = [call.args[0] for call in mocked_print.call_args_list if call.args]
            self.assertEqual(len(outputs), 2)

            first = json.loads(outputs[0])
            second = json.loads(outputs[1])
            self.assertEqual(first["file"], "a.jpg")
            self.assertEqual(first["best"]["category"], "family photo")
            self.assertEqual(second["file"], "b.jpg")
            self.assertEqual(second["error"], "failed_to_load")

    def test_sort_dry_run_reports_expected_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            in_dir = root / "input"
            out_dir = root / "output"
            in_dir.mkdir(parents=True, exist_ok=True)
            out_dir.mkdir(parents=True, exist_ok=True)

            (in_dir / "photo.jpg").write_bytes(b"fake-image")
            (in_dir / "clip.mov").write_bytes(b"fake-video")

            fake_tokens = {
                "category": "family photo",
                "year": None,
                "month": None,
                "yearmonth": None,
                "yearmo": None,
                "location": None,
            }

            with (
                patch.object(core, "_ensure_model_loaded") as ensure_model_loaded,
                patch.object(core, "load_image_for_ai", return_value=object()),
                patch.object(core, "_predict_category_internal", return_value=("family photo", 0.88, None)),
                patch.object(core, "_structure_tokens", return_value=fake_tokens),
                patch.object(core, "_render_structure", return_value=os.path.join(str(out_dir), "family photo")),
                patch("builtins.print") as mocked_print,
            ):
                rc = cli.main(
                    [
                        "--sort",
                        "--dry-run",
                        "--input",
                        str(in_dir),
                        "--output",
                        str(out_dir),
                    ]
                )

            self.assertEqual(rc, 0)
            ensure_model_loaded.assert_called_once()

            outputs = [call.args[0] for call in mocked_print.call_args_list if call.args]
            self.assertTrue(any("[dry-run] photo.jpg ->" in line for line in outputs))
            self.assertTrue(any("[dry-run] clip.mov ->" in line for line in outputs))
            self.assertIn("Images categorized: 1", outputs)
            self.assertIn("Videos handled: 1", outputs)
            self.assertIn("Failed items: 0", outputs)


if __name__ == "__main__":
    unittest.main()
