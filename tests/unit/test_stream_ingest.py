import tempfile
import unittest
from pathlib import Path

from mediasorter_stream_ingest import parse_ntfs_root_summary, parse_ntfs_stream, parse_win32_stream


class StreamIngestTests(unittest.TestCase):
    def test_parse_win32_stream_builds_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "win32.tsv"
            path.write_text("D\t0\tC:\\alpha\nF\t12\tC:\\alpha\\file.jpg\n", encoding="utf-8")
            records = parse_win32_stream(str(path), root_path="C:\\")

        self.assertEqual(len(records), 2)
        self.assertTrue(records[0].is_dir)
        self.assertEqual(records[1].size, 12)

    def test_parse_ntfs_stream_builds_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ntfs.tsv"
            path.write_text("R\t42\t5\t0\t99\timage.jpg\nR\t43\t5\t1\t0\tphotos\n", encoding="utf-8")
            records = parse_ntfs_stream(str(path))

        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].frn, 42)
        self.assertEqual(records[0].parent_frn, 5)
        self.assertFalse(records[0].is_dir)
        self.assertEqual(records[0].size, 99)
        self.assertEqual(records[1].name, "photos")
        self.assertTrue(records[1].is_dir)

    def test_parse_ntfs_root_summary_builds_summary_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ntfs_root.tsv"
            path.write_text("S\t1\t120\tUsers\nS\t0\t50\tpagefile.sys\n", encoding="utf-8")
            root_name, root_size, children = parse_ntfs_root_summary(str(path), root_path="C:\\")

        self.assertEqual(root_name, "C:\\")
        self.assertEqual(root_size, 170)
        self.assertEqual(len(children), 2)
        self.assertEqual(children[0].name, "Users")
        self.assertTrue(children[0].is_dir)


if __name__ == "__main__":
    unittest.main()
