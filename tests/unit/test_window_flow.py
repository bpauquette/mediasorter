import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

import mediasorter_window as window_mod


class WindowFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._settings_tmp = tempfile.TemporaryDirectory()
        self._settings_file_patcher = patch.object(
            window_mod, "UI_SETTINGS_FILE", Path(self._settings_tmp.name) / "ui_settings.json"
        )
        self._start_model_load_patcher = patch.object(
            window_mod.MediaSorter, "start_model_load", lambda self: None
        )
        self._maybe_run_onboarding_patcher = patch.object(
            window_mod.MediaSorter, "_maybe_run_onboarding", lambda self: None
        )
        self._settings_file_patcher.start()
        self._start_model_load_patcher.start()
        self._maybe_run_onboarding_patcher.start()
        self.window = window_mod.MediaSorter()

    def tearDown(self):
        try:
            self.window.close()
        except Exception:
            pass
        self._settings_file_patcher.stop()
        self._start_model_load_patcher.stop()
        self._maybe_run_onboarding_patcher.stop()
        self._settings_tmp.cleanup()

    def test_start_auto_thread_with_no_remaining_files_shows_done_and_skips_worker(self):
        self.window.files = ["a.jpg"]
        self.window.index = 1

        with (
            patch.object(window_mod, "AutoProcessThread") as worker_cls,
            patch.object(window_mod.QMessageBox, "information") as msg_info,
        ):
            self.window.start_auto_thread(start_index=self.window.index)

        worker_cls.assert_not_called()
        msg_info.assert_called_once()
        self.assertIn("No remaining files", msg_info.call_args[0][2])
        self.assertEqual(self.window.status_label.text(), "Status: Complete")

    def test_dismiss_interactive_with_no_remaining_files_does_not_show_zero_counts(self):
        self.window.files = ["a.jpg"]
        self.window.index = 1
        self.window.interactive_mode = True

        with (
            patch.object(window_mod, "AutoProcessThread") as worker_cls,
            patch.object(window_mod.QMessageBox, "information") as msg_info,
        ):
            self.window.dismiss_interactive()

        self.assertFalse(self.window.interactive_mode)
        worker_cls.assert_not_called()
        msg_info.assert_called_once()
        self.assertIn("No active interactive item", msg_info.call_args[0][2])

    def test_open_decision_log_opens_log_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = str(Path(tmp) / "classification_decisions.jsonl")
            with (
                patch.object(window_mod.core, "get_decision_log_path", return_value=log_path),
                patch.object(window_mod.QDesktopServices, "openUrl", return_value=True) as open_url,
            ):
                self.window.open_decision_log()
            self.assertTrue(Path(log_path).exists())
            open_url.assert_called_once()

    def test_structure_preset_updates_pattern_preview_and_summary(self):
        self.window.cmb_structure.setCurrentIndex(1)
        QApplication.processEvents()

        self.assertEqual(self.window._get_structure_pattern(), "{category}/{year}")
        self.assertFalse(self.window.edit_structure.isEnabled())
        self.assertTrue(self.window.custom_structure_box.isHidden())
        self.assertIn("Example folder result:", self.window.lbl_structure_preview.text())
        self.assertIn("ExampleCategory", self.window.lbl_structure_preview.text())
        self.assertIn(self.window.lbl_structure_preview.text(), self.window.lbl_summary_structure.text())

    def test_custom_structure_enables_manual_entry_and_updates_preview(self):
        self.window.cmb_structure.setCurrentIndex(len(self.window._structure_presets) - 1)
        QApplication.processEvents()
        self.window.edit_structure.setText("{year}/{category}")
        QApplication.processEvents()

        self.assertTrue(self.window.edit_structure.isEnabled())
        self.assertFalse(self.window.custom_structure_box.isHidden())
        self.assertEqual(self.window._get_structure_pattern(), "{year}/{category}")
        self.assertIn("OUTPUT", self.window.lbl_structure_preview.text())
        self.assertIn("2024", self.window.lbl_structure_preview.text())
        self.assertIn("ExampleCategory", self.window.lbl_structure_preview.text())

    def test_switching_away_from_custom_restores_preset_behavior(self):
        self.window.cmb_structure.setCurrentIndex(len(self.window._structure_presets) - 1)
        QApplication.processEvents()
        self.window.edit_structure.setText("{location}/{category}")
        QApplication.processEvents()

        self.window.cmb_structure.setCurrentIndex(0)
        QApplication.processEvents()

        self.assertFalse(self.window.edit_structure.isEnabled())
        self.assertTrue(self.window.custom_structure_box.isHidden())
        self.assertEqual(self.window._get_structure_pattern(), "{category}")
        self.assertIn("ExampleCategory", self.window.lbl_structure_preview.text())

    def test_focus_menu_switches_between_setup_review_and_search_panels(self):
        self.assertEqual(self.window._active_focus_view, "setup")
        self.assertFalse(self.window.folder_box.isHidden())
        self.assertTrue(self.window.image_box.isHidden())
        self.assertTrue(self.window.search_box.isHidden())
        self.assertFalse(self.window.menu_bar.isHidden())
        self.assertFalse(self.window.menu_bar.isNativeMenuBar())

        self.window._set_focus_view("review")
        QApplication.processEvents()
        self.assertEqual(self.window._active_focus_view, "review")
        self.assertTrue(self.window.folder_box.isHidden())
        self.assertFalse(self.window.image_box.isHidden())

        self.window._set_focus_view("search")
        QApplication.processEvents()
        self.assertEqual(self.window._active_focus_view, "search")
        self.assertFalse(self.window.search_box.isHidden())
        self.assertTrue(self.window.folder_box.isHidden())

    def test_ui_settings_restore_last_selected_folders_and_layout(self):
        self.window.input_folder = r"C:\Photos\Inbox"
        self.window.output_folder = r"D:\Sorted"
        self.window.cmb_structure.setCurrentIndex(1)
        QApplication.processEvents()
        self.window._save_ui_settings()
        self.window.close()

        restored = window_mod.MediaSorter()
        try:
            QApplication.processEvents()
            self.assertEqual(restored.label_input.text(), r"C:\Photos\Inbox")
            self.assertEqual(restored.label_output.text(), r"D:\Sorted")
            self.assertEqual(restored.cmb_structure.currentText(), "By category, then year")
            self.assertIn("Selected layout: By category, then year", restored.lbl_structure_selected.text())
        finally:
            restored.close()

    def test_idle_progress_does_not_spin_when_nothing_is_happening(self):
        self.assertEqual(self.window.progress.minimum(), 0)
        self.assertEqual(self.window.progress.maximum(), 1)
        self.assertEqual(self.window.progress.format(), "Idle")
        self.assertIn("Ready to configure", self.window.status_label.text())

    def test_start_processing_queues_model_load_instead_of_blocking_user(self):
        self.window.input_folder = r"C:\Photos\Inbox"
        self.window.output_folder = r"D:\Sorted"
        self.window._update_folder_labels()

        with (
            patch.object(window_mod.core, "_MODEL_READY", False),
            patch.object(window_mod.core, "_MODEL_LOAD_ERROR", None),
            patch.object(window_mod.core, "is_ai_provider_installed", return_value=True),
            patch.object(self.window, "start_model_load") as start_model_load,
        ):
            self.window.start_processing()

        start_model_load.assert_called_once()
        self.assertEqual(self.window._pending_model_action, "start_processing")

    def test_run_summary_allows_start_before_lazy_ai_load(self):
        self.window.input_folder = r"C:\Photos\Inbox"
        self.window.output_folder = r"D:\Sorted"
        with (
            patch.object(self.window, "_selected_ai_provider_id", return_value=window_mod.core.AI_PROVIDER_CLIP_LOCAL),
            patch.object(self.window, "_selected_ai_model_id", return_value="clip_vit_b32_openai"),
            patch.object(window_mod.core, "_MODEL_READY", False),
            patch.object(window_mod.core, "_MODEL_LOAD_ERROR", None),
            patch.object(window_mod.core, "is_ai_provider_installed", return_value=True),
            patch.object(window_mod.core, "get_ai_provider_display_name", return_value="Local AI"),
            patch.object(window_mod.core, "get_ai_model_display_name", return_value="CLIP ViT-B/32"),
        ):
            self.window._update_run_summary()

        self.assertTrue(self.window.btn_start.isEnabled())
        self.assertIn("will load when needed", self.window.lbl_summary_ai.text())

    def test_explanation_source_label_is_visible_for_review_entries(self):
        payload = {
            "source_path": r"C:\tmp\photo.jpg",
            "dest_path": r"C:\tmp\out\photo.jpg",
            "category": "family photo",
            "is_video": False,
            "explanation": "The AI's strongest read was 'family photo' (score 0.88).",
            "explanation_source": "category_template",
        }
        self.window._append_review_history_entry(payload)
        QApplication.processEvents()

        self.assertEqual(self.window.review_history.count(), 1)
        self.assertIn("Quick category summary", self.window.image_explanation_state_label.text())

    def test_review_summary_uses_neutral_basis_language_for_category_match(self):
        summary = self.window._review_summary_text(
            {
                "auto_category": "indoor photo",
                "current_category": "indoor photo",
                "explanation": "The AI's strongest read was 'indoor photo' (score 0.77).",
            }
        )

        self.assertIn("AI basis: strongest category match was indoor photo.", summary)
        self.assertNotIn("AI sees:", summary)

    def test_review_summary_fallback_does_not_claim_seen_objects(self):
        summary = self.window._review_summary_text(
            {
                "auto_category": "pet",
                "current_category": "pet",
                "explanation": "",
            }
        )

        self.assertEqual(summary, "AI basis: quick category summary for pet.")


if __name__ == "__main__":
    unittest.main()
