import unittest

import mediasorter_metadata_tree as meta_mod


class MetadataTreeTests(unittest.TestCase):
    def test_build_metadata_tree_rolls_up_sizes(self):
        records = [
            meta_mod.FilesystemMetadataRecord(frn=1, parent_frn=0, name="Users", is_dir=True),
            meta_mod.FilesystemMetadataRecord(frn=2, parent_frn=1, name="bryan", is_dir=True),
            meta_mod.FilesystemMetadataRecord(frn=3, parent_frn=2, name="a.jpg", is_dir=False, size=100),
            meta_mod.FilesystemMetadataRecord(frn=4, parent_frn=2, name="b.jpg", is_dir=False, size=50),
        ]
        root = meta_mod.build_metadata_tree(records, root_name="C:\\", root_path="C:\\")

        self.assertEqual(root.size, 150)
        self.assertEqual(root.children[0].name, "Users")
        self.assertEqual(root.children[0].size, 150)
        self.assertEqual(root.children[0].children[0].size, 150)

    def test_missing_parent_attaches_to_root(self):
        records = [
            meta_mod.FilesystemMetadataRecord(frn=99, parent_frn=12345, name="orphan.bin", is_dir=False, size=7),
        ]
        root = meta_mod.build_metadata_tree(records, root_name="C:\\", root_path="C:\\")

        self.assertEqual(len(root.children), 1)
        self.assertEqual(root.children[0].name, "orphan.bin")
        self.assertEqual(root.size, 7)

    def test_flatten_metadata_tree_round_trip(self):
        records = [
            meta_mod.FilesystemMetadataRecord(frn=1, parent_frn=0, name="Windows", is_dir=True),
            meta_mod.FilesystemMetadataRecord(frn=2, parent_frn=1, name="explorer.exe", is_dir=False, size=42),
        ]
        root = meta_mod.build_metadata_tree(records, root_name="C:\\", root_path="C:\\")
        flattened = meta_mod.flatten_metadata_tree(root)

        by_frn = {record.frn: record for record in flattened}
        self.assertEqual(by_frn[1].name, "Windows")
        self.assertEqual(by_frn[2].size, 42)


if __name__ == "__main__":
    unittest.main()
