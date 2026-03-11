import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from mediasorter_ntfs import NTFSEnumerationProbe
import mediasorter_shell as shell_mod


class ShellWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._settings_tmp = tempfile.TemporaryDirectory()
        self._settings_patcher = patch.object(
            shell_mod, "SHELL_SETTINGS_FILE", Path(self._settings_tmp.name) / "shell_settings.json"
        )
        self._settings_patcher.start()
        self.window = shell_mod.MediaSorter()

    def tearDown(self):
        try:
            self.window.close()
        except Exception:
            pass
        self._settings_patcher.stop()
        self._settings_tmp.cleanup()

    def test_top_level_menu_labels_match_agreed_structure(self):
        labels = [action.text() for action in self.window.menuBar().actions()]
        self.assertEqual(labels, ["Files", "Edit", "View", "Run", "Help"])

    def test_files_menu_contains_agreed_actions(self):
        menu = self.window.files_menu
        labels = [action.text() for action in menu.actions() if not action.isSeparator()]
        self.assertEqual(labels, ["Input Folder...", "Output Folder...", "Check Disk Space", "Exit"])

    def test_edit_menu_contains_agreed_actions(self):
        menu = self.window.edit_menu
        labels = [action.text() for action in menu.actions() if not action.isSeparator()]
        self.assertEqual(labels, ["Categories...", "User Preferences..."])

    def test_view_menu_contains_agreed_actions(self):
        menu = self.window.view_menu
        labels = [action.text() for action in menu.actions() if not action.isSeparator()]
        self.assertEqual(labels, ["Classification Log", "Current Item", "Statistics"])

    def test_run_menu_contains_agreed_actions(self):
        menu = self.window.run_menu
        labels = [action.text() for action in menu.actions() if not action.isSeparator()]
        self.assertEqual(labels, ["Start", "Stop"])

    def test_help_menu_contains_agreed_actions(self):
        menu = self.window.help_menu
        labels = [action.text() for action in menu.actions() if not action.isSeparator()]
        self.assertEqual(labels, ["About", "Welcome", "Check for Updates", "Privacy"])

    def test_center_area_shows_welcome_purpose_and_usage_text(self):
        self.assertEqual(self.window.subtitle_label.text(), "Organize your photo library")
        self.assertIn("helps you organize a photo library", self.window.intro_label.text())
        self.assertIn("Use the Files menu", self.window.welcome_label.text())

    def test_window_size_is_restored_from_last_run(self):
        self.window.resize(1111, 777)
        self.window._save_shell_settings()
        self.window.close()

        restored = shell_mod.MediaSorter()
        try:
            self.assertEqual(restored.width(), 1111)
            self.assertEqual(restored.height(), 777)
        finally:
            restored.close()

    def test_available_drives_returns_usage_records(self):
        with patch.object(shell_mod.os.path, "exists", side_effect=lambda p: p in {"C:\\", "D:\\"}), patch.object(
            shell_mod.shutil,
            "disk_usage",
            side_effect=lambda p: (1000, 400, 600) if p == "C:\\" else (2000, 1200, 800),
        ):
            drives = self.window._available_drives()

        self.assertEqual([d["path"] for d in drives], ["C:\\", "D:\\"])
        self.assertEqual(drives[0]["free"], 600)

    def test_open_drive_treemap_prompts_for_uac_on_ntfs_access_denied(self):
        probe = NTFSEnumerationProbe(
            drive="C:\\",
            filesystem="NTFS",
            journal_present=True,
            volume_open_ok=False,
            query_journal_ok=False,
            enum_usn_ok=False,
            open_error=5,
            notes="denied",
        )
        with patch.object(shell_mod, "probe_ntfs_enumerator", return_value=probe), patch.object(
            shell_mod.QMessageBox, "question", return_value=shell_mod.QMessageBox.Yes
        ), patch.object(self.window, "_launch_elevated_treemap", return_value=True) as launch, patch.object(
            shell_mod, "TreemapDialog"
        ) as dialog_cls:
            self.window._open_drive_treemap("C:\\")

        launch.assert_called_once_with("C:\\")
        dialog_cls.assert_not_called()

    def test_open_drive_treemap_skips_uac_prompt_when_not_needed(self):
        probe = NTFSEnumerationProbe(
            drive="C:\\",
            filesystem="NTFS",
            journal_present=True,
            volume_open_ok=True,
            query_journal_ok=True,
            enum_usn_ok=True,
            open_error=0,
            notes="ok",
        )
        with patch.object(shell_mod, "probe_ntfs_enumerator", return_value=probe), patch.object(
            shell_mod, "TreemapDialog"
        ) as dialog_cls:
            dialog_cls.return_value.exec.return_value = 0
            self.window._open_drive_treemap("C:\\")

        dialog_cls.assert_called_once_with("C:\\", self.window)
        dialog_cls.return_value.exec.assert_called_once()


if __name__ == "__main__":
    unittest.main()
