import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import mediasorter_core as core


class PeopleGroupingTests(unittest.TestCase):
    def test_face_support_status_reports_missing_face_modules(self):
        fake_cv2 = SimpleNamespace(FaceDetectorYN=object())
        with patch.object(core, "HAS_CV2", True), patch.object(core, "cv2", fake_cv2):
            status = core.get_face_support_status()

        self.assertFalse(status["supported"])
        self.assertTrue(status["has_cv2"])
        self.assertTrue(status["has_face_detector"])
        self.assertFalse(status["has_face_recognizer"])
        self.assertIn("missing required face modules", status["detail"])

    def test_people_add_face_matches_known_person(self):
        known = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            core, "_load_people_db", return_value={"Alice": {"count": 3, "embedding": known.tolist()}}
        ):
            worker = core.AutoProcessThread([], tmp, tmp, convert_videos=False, enable_people=True)

        worker._people_add_face("img1.jpg", known, (1, 2, 3, 4))

        self.assertEqual(len(worker.people_clusters), 1)
        cluster = worker.people_clusters[0]
        self.assertEqual(cluster["name"], "Alice")
        self.assertEqual(cluster["count"], 1)
        self.assertEqual(cluster["files"], {"img1.jpg"})

    def test_people_add_face_merges_unknown_cluster_by_similarity(self):
        emb_a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        emb_b = np.array([0.98, 0.02, 0.0], dtype=np.float32)
        emb_b = emb_b / np.linalg.norm(emb_b)
        with tempfile.TemporaryDirectory() as tmp, patch.object(core, "_load_people_db", return_value={}):
            worker = core.AutoProcessThread([], tmp, tmp, convert_videos=False, enable_people=True)

        worker._people_add_face("img1.jpg", emb_a, (1, 2, 3, 4))
        worker._people_add_face("img2.jpg", emb_b, (5, 6, 7, 8))

        self.assertEqual(len(worker.people_clusters), 1)
        cluster = worker.people_clusters[0]
        self.assertIsNone(cluster["name"])
        self.assertEqual(cluster["count"], 2)
        self.assertEqual(cluster["files"], {"img1.jpg", "img2.jpg"})


if __name__ == "__main__":
    unittest.main()
