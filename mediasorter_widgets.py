import hashlib
import os
import shutil

import numpy as np
from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QTimer, QVariantAnimation, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QGraphicsObject,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
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
class _PixCard(QGraphicsObject):
    """A pixmap-backed QGraphicsObject so we can animate its position."""

    def __init__(self, pixmap: QPixmap, parent=None):
        super().__init__(parent)
        self._pm = pixmap

    def boundingRect(self):
        return self._pm.rect()

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(0, 0, self._pm)

class SortingStacksView(QGraphicsView):
    """Professional stacks view: calm motion, stable lanes, live counts."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(
            self.renderHints()
            | QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumHeight(260)
        self.setStyleSheet("background: #0c1118; border: 1px solid #273041;")
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        self._running = False
        self._queue = []
        self._animating = False
        self._incoming_tick = 0

        self._max_stacks = 6
        self._order = []
        self._stacks = {}
        self._divider_x = 520
        self._incoming_origin = QPointF(34.0, 104.0)
        self._incoming_lane_h = 120.0

        self._init_static()

    def set_running(self, running: bool) -> None:
        running = bool(running)
        if running == self._running:
            return
        self._running = running
        if self._running:
            self.reset()
            if self._queue and not self._animating:
                self._start_next()
        else:
            self._queue.clear()
            self._animating = False

    def reset(self) -> None:
        self._queue.clear()
        self._animating = False
        self._order.clear()
        self._stacks.clear()
        self._init_static()

    def _init_static(self):
        sc = self.scene()
        sc.clear()

        w = max(860, int(self.viewport().width() or 900))
        h = max(240, int(self.viewport().height() or 260))
        sc.setSceneRect(0, 0, w, h)

        title = QGraphicsTextItem("Stacks")
        title.setDefaultTextColor(QColor("#d7dee9"))
        title.setPos(18, 12)
        sc.addItem(title)

        hint = QGraphicsTextItem("Incoming media moves into category lanes.")
        hint.setDefaultTextColor(QColor("#8b95a7"))
        hint.setPos(18, 34)
        sc.addItem(hint)

        divider_x = int(w * 0.56)
        self._divider_x = divider_x

        div = QGraphicsRectItem(divider_x, 10, 1, h - 20)
        div.setBrush(QColor("#3b4659"))
        div.setOpacity(0.32)
        div.setPen(Qt.NoPen)
        sc.addItem(div)

        lane_y = 74
        lane_h = max(100, h - lane_y - 18)
        lane = QGraphicsRectItem(16, lane_y, max(140, divider_x - 34), lane_h)
        lane.setBrush(QColor(21, 29, 40, 150))
        lane.setPen(Qt.NoPen)
        sc.addItem(lane)

        self._incoming_origin = QPointF(30.0, lane_y + 10.0)
        self._incoming_lane_h = float(max(48, lane_h - 20))

        incoming = QGraphicsTextItem("Incoming")
        incoming.setDefaultTextColor(QColor("#9ba5b6"))
        incoming.setPos(20, lane_y - 22)
        sc.addItem(incoming)

        stacks_lbl = QGraphicsTextItem("Categories")
        stacks_lbl.setDefaultTextColor(QColor("#9ba5b6"))
        stacks_lbl.setPos(divider_x + 16, 12)
        sc.addItem(stacks_lbl)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Re-layout static + stack metadata.
        cats = list(self._order)
        counts = {c: (self._stacks.get(c, {}).get("count", 0)) for c in cats}
        self._init_static()
        self._stacks = {}
        for c in cats:
            self._ensure_stack(c)
            self._stacks[c]["count"] = int(counts.get(c, 0) or 0)
            self._update_stack_label(c)
        self._layout_stacks()

    def enqueue(self, file_path: str, category: str, is_video: bool = False):
        if not self._running:
            return
        self._queue.append((file_path, category or ("Videos" if is_video else "Uncategorized"), bool(is_video)))
        if len(self._queue) > 50:
            self._queue = self._queue[-12:]
        if not self._animating:
            self._start_next()

    def _ensure_stack(self, category: str):
        c = (category or "Uncategorized").strip() or "Uncategorized"
        if c in self._stacks:
            if c in self._order:
                self._order.remove(c)
            self._order.append(c)
            self._layout_stacks()
            return

        self._order.append(c)
        while len(self._order) > self._max_stacks:
            old = self._order.pop(0)
            st = self._stacks.pop(old, None)
            if st:
                try:
                    self.scene().removeItem(st["rect"])
                    self.scene().removeItem(st["title"])
                    self.scene().removeItem(st["count_text"])
                    for it in st["items"]:
                        self.scene().removeItem(it)
                except Exception:
                    pass

        palette = self._category_palette(c)

        rect = QGraphicsRectItem(0, 0, 250, 56)
        rect.setBrush(palette["bg"])
        rect.setOpacity(0.92)
        rect.setPen(Qt.NoPen)
        self.scene().addItem(rect)

        accent = QGraphicsRectItem(0, 0, 4, 56)
        accent.setBrush(palette["accent"])
        accent.setPen(Qt.NoPen)
        self.scene().addItem(accent)

        title = QGraphicsTextItem(c)
        title.setDefaultTextColor(QColor("#f3f6fb"))
        title.setScale(0.80)
        self.scene().addItem(title)

        cnt = QGraphicsTextItem("0")
        cnt.setDefaultTextColor(QColor("#9db6ff"))
        cnt.setScale(0.80)
        self.scene().addItem(cnt)

        self._stacks[c] = {
            "rect": rect,
            "accent": accent,
            "title": title,
            "count_text": cnt,
            "items": [],
            "count": 0,
            "palette": palette,
            "pulse_anim": None,
        }
        self._layout_stacks()

    def _layout_stacks(self):
        try:
            rect = self.sceneRect()
            base_x = float(self._divider_x + 16)
            y0 = 42.0
            gap = 8.0
            visible = self._order[-self._max_stacks:]
            count = max(1, len(visible))
            avail_h = max(120.0, float(rect.height()) - y0 - 16.0)
            card_h = max(46.0, min(74.0, (avail_h - gap * (count - 1)) / count))
            card_w = max(210.0, float(rect.width()) - base_x - 16.0)
            for i, c in enumerate(visible):
                st = self._stacks[c]
                y = y0 + i * (card_h + gap)
                st["rect"].setRect(base_x, y, card_w, card_h)
                st["accent"].setRect(base_x, y, 4, card_h)
                st["title"].setPos(base_x + 10, y + 6)
                st["count_text"].setPos(base_x + card_w - 76, y + 6)
                self._layout_stack_items(c)
        except Exception:
            pass

    def _update_stack_label(self, category: str):
        try:
            st = self._stacks.get(category)
            if not st:
                return
            n = int(st.get("count") or 0)
            st["count_text"].setPlainText(f"{n:>3} items")
        except Exception:
            pass

    def _make_thumb_pixmap(self, file_path: str, is_video: bool) -> QPixmap:
        if is_video:
            return self._make_video_pixmap(54)
        img = load_image_for_ai(file_path)
        if img is None:
            pm = QPixmap(54, 54)
            pm.fill(QColor("#0f141d"))
            return pm
        pm = pil_to_qpixmap(img, max_size=(52, 52))
        out = QPixmap(56, 56)
        out.fill(Qt.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(15, 20, 28, 200))
        p.drawRoundedRect(0, 0, 56, 56, 8, 8)
        p.drawPixmap(2, 2, pm)
        p.end()
        return out

    def _make_video_pixmap(self, size: int = 54) -> QPixmap:
        pm = QPixmap(size, size)
        pm.fill(QColor("#121922"))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#273244"))
        p.drawRoundedRect(2, 2, size - 4, size - 4, 10, 10)
        p.setPen(QColor("#dce3ef"))
        p.drawText(pm.rect(), Qt.AlignCenter, "VIDEO")
        p.end()
        return pm

    def _category_palette(self, category: str) -> dict:
        h = int(hashlib.md5(str(category).encode("utf-8", errors="ignore")).hexdigest()[:6], 16)
        r = 80 + (h % 110)
        g = 110 + ((h // 7) % 90)
        b = 170 + ((h // 13) % 70)
        accent = QColor(r, g, b, 230)
        bg = QColor(29, 36, 49, 220)
        bg_hi = QColor(min(255, r // 2 + 80), min(255, g // 2 + 90), min(255, b // 2 + 100), 190)
        return {"accent": accent, "bg": bg, "bg_hi": bg_hi}

    def _layout_stack_items(self, category: str):
        st = self._stacks.get(category)
        if not st:
            return
        r = st["rect"].rect()
        items = st.get("items", [])
        if not items:
            return
        item_w = 56.0
        base_x = float(r.x() + r.width() - item_w - 8.0)
        base_y = float(r.y() + max(0.0, (r.height() - item_w) * 0.5))
        for idx, it in enumerate(items[-6:]):
            shift = float(min(5, idx) * 9)
            it.setPos(base_x - shift, base_y + (idx % 2) * 1.5)
            it.setOpacity(max(0.45, 0.98 - idx * 0.07))
            it.setZValue(100 + idx)

    def _pile_target(self, category: str, file_path: str):
        st = self._stacks.get(category)
        if not st:
            return QPointF(0, 0), 0.0
        r = st["rect"].rect()
        idx = int(st.get("count") or 0)
        x = float(r.x() + r.width() - 64.0 - min(5, idx) * 9.0)
        y = float(r.y() + max(0.0, (r.height() - 56.0) * 0.5) + (idx % 2) * 1.5)
        return QPointF(x, y), 0.0

    def _next_incoming_pos(self) -> QPointF:
        try:
            lane_h = max(36.0, float(self._incoming_lane_h))
            step = min(32.0, max(18.0, lane_h / 5.5))
            y = float(self._incoming_origin.y() + (self._incoming_tick % 5) * step)
            self._incoming_tick += 1
            return QPointF(float(self._incoming_origin.x()), y)
        except Exception:
            return QPointF(34.0, 104.0)

    def _pulse_stack(self, category: str):
        st = self._stacks.get(category)
        if not st:
            return
        rect = st["rect"]
        pal = st.get("palette") or {}
        base = pal.get("bg", QColor(29, 36, 49, 220))
        hi = pal.get("bg_hi", QColor(64, 84, 120, 200))

        anim = QVariantAnimation(self)
        anim.setDuration(220)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.InOutCubic)

        def on_change(v):
            t = float(v)
            if t > 0.5:
                t = 1.0 - t
            mix = max(0.0, min(1.0, t * 2.0))
            c = QColor(
                int(base.red() + (hi.red() - base.red()) * mix),
                int(base.green() + (hi.green() - base.green()) * mix),
                int(base.blue() + (hi.blue() - base.blue()) * mix),
                int(base.alpha() + (hi.alpha() - base.alpha()) * mix),
            )
            rect.setBrush(c)

        def on_done():
            rect.setBrush(base)

        anim.valueChanged.connect(on_change)
        anim.finished.connect(on_done)
        st["pulse_anim"] = anim
        anim.start()

    def _start_next(self):
        if not self._queue:
            self._animating = False
            return
        self._animating = True

        file_path, category, is_video = self._queue.pop(0)
        self._ensure_stack(category)
        self._layout_stacks()

        pm = self._make_thumb_pixmap(file_path, is_video)
        item = _PixCard(pm)
        item.setOpacity(0.0)
        start_pos = self._next_incoming_pos()
        item.setPos(start_pos)
        self.scene().addItem(item)

        target, _ = self._pile_target(category, file_path)

        # Calm reveal + direct movement into category lane.
        fade_in = QVariantAnimation(self)
        fade_in.setDuration(120)
        fade_in.setStartValue(0.15)
        fade_in.setEndValue(1.0)
        fade_in.valueChanged.connect(lambda v: item.setOpacity(float(v)))

        a = QPropertyAnimation(item, b"pos", self)
        a.setDuration(430)
        a.setStartValue(start_pos)
        a.setEndValue(target)
        a.setEasingCurve(QEasingCurve.InOutCubic)

        def after_fade():
            a.start()

        def done():
            st = self._stacks.get(category)
            if st is not None:
                st["items"].append(item)
                st["count"] = int(st.get("count") or 0) + 1
                self._update_stack_label(category)
                if len(st["items"]) > 8:
                    old = st["items"].pop(0)
                    try:
                        self.scene().removeItem(old)
                    except Exception:
                        pass
                self._layout_stack_items(category)
                self._pulse_stack(category)
            self._animating = False
            QTimer.singleShot(0, self._start_next)

        fade_in.finished.connect(after_fade)
        a.finished.connect(done)
        fade_in.start()

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

