import os
import shutil

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from mediasorter_core import (
    _load_people_db,
    _safe_folder_name,
    _save_people_db,
    _unique_dest_path,
    load_image_for_ai,
    pil_to_qpixmap,
)
import mediasorter_core as core
class PeopleReviewDialog(QDialog):
    def __init__(self, parent, clusters: list, output_map: dict, output_root: str):
        super().__init__(parent)
        self.setWindowTitle("Identify People")
        self.resize(780, 520)
        self._clusters = clusters
        self._output_map = output_map or {}
        self._output_root = output_root
        self._labeled = 0

        self.list_clusters = QListWidget()
        for i, cl in enumerate(self._clusters):
            name = cl.get("name") or "Unknown"
            cnt = int(cl.get("count") or 0)
            self.list_clusters.addItem(f"{i+1}. {name} ({cnt})")

        self.face_preview = QLabel("Select a face cluster")
        self.face_preview.setAlignment(Qt.AlignCenter)
        self.face_preview.setMinimumHeight(220)

        self.lbl_info = QLabel("")
        self.edit_name = QLineEdit()
        self.edit_name.setPlaceholderText("Who is this? (type a name, e.g. Mom, John, Sarah)")

        btn_assign = QPushButton("Assign Name")
        btn_skip = QPushButton("Skip")
        btn_done = QPushButton("Done")

        btn_assign.clicked.connect(self.assign_current)
        btn_skip.clicked.connect(self.skip_current)
        btn_done.clicked.connect(self.accept)

        right = QVBoxLayout()
        right.addWidget(self.face_preview)
        right.addWidget(self.lbl_info)
        right.addWidget(self.edit_name)
        btns = QHBoxLayout()
        btns.addWidget(btn_assign)
        btns.addWidget(btn_skip)
        btns.addWidget(btn_done)
        right.addLayout(btns)

        layout = QHBoxLayout()
        layout.addWidget(self.list_clusters, 1)
        right_box = QVBoxLayout()
        right_box.addLayout(right)
        layout.addLayout(right_box, 2)
        self.setLayout(layout)

        self.list_clusters.currentRowChanged.connect(self.on_select)
        if self.list_clusters.count() > 0:
            self.list_clusters.setCurrentRow(0)

    @property
    def labeled_count(self) -> int:
        return int(self._labeled)

    def _thumb_for_cluster(self, cl) -> QPixmap:
        try:
            p = cl.get("rep_path")
            bbox = cl.get("rep_bbox")
            if not p or not bbox:
                return QPixmap()
            img = load_image_for_ai(p)
            if img is None:
                return QPixmap()
            x, y, w, h = bbox
            x = max(0, int(x))
            y = max(0, int(y))
            w = max(1, int(w))
            h = max(1, int(h))
            crop = img.crop((x, y, x + w, y + h)).convert("RGB")
            return pil_to_qpixmap(crop, max_size=(200, 200))
        except Exception:
            return QPixmap()

    def on_select(self, row: int):
        if row < 0 or row >= len(self._clusters):
            return
        cl = self._clusters[row]
        pm = self._thumb_for_cluster(cl)
        if not pm.isNull():
            self.face_preview.setPixmap(pm)
        else:
            self.face_preview.setText("No preview")

        name = cl.get("name") or "Unknown"
        cnt = int(cl.get("count") or 0)
        self.lbl_info.setText(f"Cluster: {name}  Faces: {cnt}")
        if cl.get("name"):
            self.edit_name.setText(str(cl.get("name")))
        else:
            self.edit_name.clear()

    def _copy_cluster_to_person_folder(self, cl, person: str):
        person = _safe_folder_name(person)
        dest_dir = os.path.join(self._output_root, "family photo", person)
        os.makedirs(dest_dir, exist_ok=True)
        for src_in in sorted(list(cl.get("files") or [])):
            src = self._output_map.get(src_in) or src_in
            if not os.path.exists(src):
                continue
            dest = _unique_dest_path(dest_dir, os.path.basename(src))
            try:
                shutil.copy2(src, dest)
            except Exception:
                pass

    def _merge_person_prototype(self, person: str, centroid: np.ndarray):
        try:
            person = person.strip()
            if not person:
                return
            db = _load_people_db()
            vec = np.asarray(centroid, dtype=np.float32).flatten()
            n = float(np.linalg.norm(vec))
            if n > 0:
                vec = vec / n
            existing = db.get(person)
            if isinstance(existing, dict) and isinstance(existing.get("embedding"), list):
                old = np.array([float(x) for x in existing.get("embedding") or []], dtype=np.float32)
                cnt = int(existing.get("count") or 1)
                if old.size == vec.size and cnt > 0:
                    new = (old * cnt + vec) / float(cnt + 1)
                    nn = float(np.linalg.norm(new))
                    if nn > 0:
                        new = new / nn
                    db[person] = {"count": cnt + 1, "embedding": new.tolist()}
                else:
                    db[person] = {"count": 1, "embedding": vec.tolist()}
            else:
                db[person] = {"count": 1, "embedding": vec.tolist()}
            _save_people_db(db)
            core.PEOPLE_DB = db
        except Exception:
            pass

    def assign_current(self):
        row = self.list_clusters.currentRow()
        if row < 0 or row >= len(self._clusters):
            return
        person = (self.edit_name.text() or "").strip()
        if not person:
            QMessageBox.information(self, "Name Required", "Type a name first.")
            return
        cl = self._clusters[row]
        cl["name"] = person
        self._merge_person_prototype(person, cl.get("centroid"))
        self._copy_cluster_to_person_folder(cl, person)
        self._labeled += 1
        self.list_clusters.item(row).setText(f"{row+1}. {person} ({int(cl.get('count') or 0)})")
        self.skip_current()

    def skip_current(self):
        row = self.list_clusters.currentRow()
        nxt = row + 1
        if nxt < self.list_clusters.count():
            self.list_clusters.setCurrentRow(nxt)
        else:
            self.accept()

