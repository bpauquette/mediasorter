import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QApplication

import mediasorter_shell as shell_mod
import mediasorter_treemap as treemap_mod


class TreemapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_squarify_returns_one_rect_per_value(self):
        rects = treemap_mod.squarify_rects([50, 30, 20], QRectF(0, 0, 100, 80))
        self.assertEqual(len(rects), 3)
        for rect in rects:
            self.assertGreaterEqual(rect.width(), 0.0)
            self.assertGreaterEqual(rect.height(), 0.0)

    def test_squarify_uses_short_side_layout_from_paper(self):
        rects = treemap_mod.squarify_rects([60, 40], QRectF(0, 0, 200, 100))
        self.assertEqual(len(rects), 2)
        self.assertAlmostEqual(rects[0].x(), 0.0)
        self.assertAlmostEqual(rects[1].x(), rects[0].width(), places=4)
        self.assertAlmostEqual(rects[0].height(), 100.0, places=4)
        self.assertAlmostEqual(rects[1].height(), 100.0, places=4)

    def test_cache_round_trip_restores_tree(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_dir = Path(tmp_dir)
            root = treemap_mod.TreeNode(
                name="C:\\",
                path="C:\\",
                size=123,
                is_dir=True,
                complete=True,
                children=[
                    treemap_mod.TreeNode(
                        name="Photos",
                        path="C:\\Photos",
                        size=100,
                        is_dir=True,
                        complete=True,
                        children=[
                            treemap_mod.TreeNode(
                                name="one.jpg",
                                path="C:\\Photos\\one.jpg",
                                size=100,
                                is_dir=False,
                                complete=True,
                            )
                        ],
                    )
                ],
            )
            with patch.object(treemap_mod, "TREEMAP_CACHE_DIR", cache_dir):
                treemap_mod.save_tree_cache("C:\\", root)
                restored = treemap_mod.load_tree_cache("C:\\")

        self.assertIsNotNone(restored)
        self.assertEqual(restored.path, "C:\\")
        self.assertEqual(restored.children[0].path, "C:\\Photos")
        self.assertEqual(restored.children[0].children[0].name, "one.jpg")

    def test_oversized_cache_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_dir = Path(tmp_dir)
            cache_file = cache_dir / "x.json"
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file.write_text("{}", encoding="utf-8")
            with patch.object(treemap_mod, "TREEMAP_CACHE_DIR", cache_dir), patch.object(
                treemap_mod, "_cache_file_for_path", return_value=cache_file
            ), patch.object(treemap_mod, "MAX_CACHE_BYTES", 1):
                restored = treemap_mod.load_tree_cache("C:\\")

        self.assertIsNone(restored)

    def test_provisional_directory_node_is_marked_refining(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            Path(tmp_dir, "a.txt").write_text("hello", encoding="utf-8")
            node = treemap_mod._make_provisional_directory_node(tmp_dir)

        self.assertTrue(node.is_dir)
        self.assertEqual(node.kind, "pending_dir")
        self.assertFalse(node.complete)
        self.assertGreaterEqual(node.size, 1)

    def test_replace_child_node_swaps_existing_entry(self):
        old = treemap_mod.TreeNode("Temp", "C:\\Temp", 1, True, complete=False, kind="pending_dir")
        new = treemap_mod.TreeNode("Temp", "C:\\Temp", 10, True, complete=True)
        children = treemap_mod._replace_child_node([old], new)
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0].size, 10)
        self.assertTrue(children[0].complete)

    def test_root_decoration_adds_free_space_region(self):
        root = treemap_mod.TreeNode(
            name="C:\\",
            path="C:\\",
            size=400,
            is_dir=True,
            complete=True,
            children=[
                treemap_mod.TreeNode(
                    name="data.bin",
                    path="C:\\data.bin",
                    size=400,
                    is_dir=False,
                    complete=True,
                )
            ],
        )
        with patch.object(treemap_mod.shutil, "disk_usage", return_value=(1000, 700, 300)):
            decorated = treemap_mod._decorate_root_with_disk_regions("C:\\", root)

        labels = [child.name for child in decorated.children]
        self.assertIn("Free space", labels)
        self.assertIn("Unscanned or protected", labels)

    def test_root_layout_places_free_space_on_right(self):
        children = [
            treemap_mod.TreeNode("Used A", "C:\\A", 600, True, complete=True),
            treemap_mod.TreeNode("Used B", "C:\\B", 200, True, complete=True),
            treemap_mod.TreeNode("Free space", "", 200, False, complete=True, kind="free_space"),
        ]
        laid_out = treemap_mod.layout_root_nodes(children, QRectF(0, 0, 1000, 500))
        by_name = {node.name: rect for rect, node in laid_out}
        self.assertGreater(by_name["Free space"].x(), by_name["Used A"].x())
        self.assertGreater(by_name["Free space"].x(), by_name["Used B"].x())
        self.assertAlmostEqual(by_name["Free space"].height(), 500.0, places=4)

    def test_explorer_target_uses_parent_folder_for_files(self):
        node = treemap_mod.TreeNode("file.txt", "C:\\Temp\\file.txt", 10, False, complete=True)
        self.assertEqual(treemap_mod._explorer_target_for_node(node), "C:\\Temp")

    def test_explorer_target_uses_directory_for_folders(self):
        node = treemap_mod.TreeNode("Temp", "C:\\Temp", 10, True, complete=True)
        self.assertEqual(treemap_mod._explorer_target_for_node(node), "C:\\Temp")

    def test_classify_special_kind_marks_system_managed_paths(self):
        self.assertEqual(treemap_mod._classify_special_kind("C:\\pagefile.sys", False), "system")
        self.assertEqual(treemap_mod._classify_special_kind("C:\\System Volume Information", True), "system")
        self.assertEqual(treemap_mod._classify_special_kind("C:\\Users", True), "node")

    def test_open_drive_treemap_creates_dialog(self):
        shell = shell_mod.MediaSorter()
        try:
            with patch.object(shell_mod, "TreemapDialog") as dialog_cls:
                dialog_cls.return_value.exec.return_value = 0
                shell._open_drive_treemap("C:\\")
            dialog_cls.assert_called_once_with("C:\\", shell)
            dialog_cls.return_value.exec.assert_called_once()
        finally:
            shell.close()


if __name__ == "__main__":
    unittest.main()
