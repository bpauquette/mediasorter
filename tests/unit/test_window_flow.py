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
        self._start_model_load_patcher = patch.object(
            window_mod.MediaSorter, "start_model_load", lambda self: None
        )
        self._start_model_load_patcher.start()
        self.window = window_mod.MediaSorter()

    def tearDown(self):
        try:
            self.window.close()
        except Exception:
            pass
        self._start_model_load_patcher.stop()

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


if __name__ == "__main__":
    unittest.main()
