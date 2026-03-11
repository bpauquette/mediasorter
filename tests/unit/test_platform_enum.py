import unittest
from unittest.mock import patch

import mediasorter_platform_enum as enum_mod


class PlatformEnumerationTests(unittest.TestCase):
    def test_windows_ntfs_with_journal_prefers_mft_usn_strategy(self):
        with patch.object(enum_mod.platform, "system", return_value="Windows"), patch.object(
            enum_mod, "_get_windows_filesystem_name", return_value="NTFS"
        ), patch.object(enum_mod, "_query_usn_journal_available", return_value=True):
            strategy = enum_mod.detect_enumeration_strategy("C:\\")

        self.assertEqual(strategy.backend_id, "windows_ntfs_mft_usn")

    def test_windows_non_ntfs_uses_fallback(self):
        with patch.object(enum_mod.platform, "system", return_value="Windows"), patch.object(
            enum_mod, "_get_windows_filesystem_name", return_value="FAT32"
        ):
            strategy = enum_mod.detect_enumeration_strategy("E:\\")

        self.assertEqual(strategy.backend_id, "windows_scandir_fallback")

    def test_linux_prefers_native_directory_enumeration(self):
        with patch.object(enum_mod.platform, "system", return_value="Linux"):
            strategy = enum_mod.detect_enumeration_strategy("/mnt/data")

        self.assertEqual(strategy.backend_id, "linux_scandir_inotify")

    def test_macos_prefers_bulkattr_and_fsevents(self):
        with patch.object(enum_mod.platform, "system", return_value="Darwin"):
            strategy = enum_mod.detect_enumeration_strategy("/Volumes/Macintosh HD")

        self.assertEqual(strategy.backend_id, "macos_bulkattr_fsevents")


if __name__ == "__main__":
    unittest.main()
