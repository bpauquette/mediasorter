import unittest
from unittest.mock import patch

import mediasorter_ntfs as ntfs_mod


class NTFSProbeTests(unittest.TestCase):
    def test_non_ntfs_drive_returns_not_applicable_probe(self):
        with patch.object(ntfs_mod, "_get_windows_filesystem_name", return_value="FAT32"), patch.object(
            ntfs_mod, "_query_usn_journal_available", return_value=False
        ):
            probe = ntfs_mod.probe_ntfs_enumerator("E:\\")

        self.assertEqual(probe.filesystem, "FAT32")
        self.assertFalse(probe.volume_open_ok)
        self.assertIn("not NTFS", probe.notes)

    def test_ntfs_open_failure_reports_error(self):
        with patch.object(ntfs_mod, "_get_windows_filesystem_name", return_value="NTFS"), patch.object(
            ntfs_mod, "_query_usn_journal_available", return_value=True
        ), patch.object(ntfs_mod, "_open_volume_handle", return_value=(None, 5)):
            probe = ntfs_mod.probe_ntfs_enumerator("C:\\")

        self.assertEqual(probe.open_error, 5)
        self.assertFalse(probe.volume_open_ok)

    def test_ntfs_probe_reports_query_and_enum_status(self):
        with patch.object(ntfs_mod, "_get_windows_filesystem_name", return_value="NTFS"), patch.object(
            ntfs_mod, "_query_usn_journal_available", return_value=True
        ), patch.object(ntfs_mod, "_open_volume_handle", return_value=(123, 0)), patch.object(
            ntfs_mod, "_query_usn_journal", return_value=(True, 0)
        ), patch.object(ntfs_mod, "_enum_usn_once", return_value=(False, 1)), patch.object(
            ntfs_mod, "_close_handle"
        ):
            probe = ntfs_mod.probe_ntfs_enumerator("C:\\")

        self.assertTrue(probe.volume_open_ok)
        self.assertTrue(probe.query_journal_ok)
        self.assertFalse(probe.enum_usn_ok)
        self.assertEqual(probe.enum_error, 1)


if __name__ == "__main__":
    unittest.main()
