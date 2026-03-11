from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
import subprocess
import shutil
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal, QRectF, QUrl
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from mediasorter_ntfs import probe_ntfs_enumerator, query_ntfs_journal_state


TREEMAP_CACHE_DIR = Path(__file__).resolve().parent / ".treemap_cache"
TREEMAP_PROFILE_LOG = TREEMAP_CACHE_DIR / "treemap_profile.log"
TREEMAP_TREE_CACHE_VERSION = 1
MIN_RENDER_EDGE = 2.0
MIN_RECURSE_EDGE = 14.0
NTFS_HELPER_EXE = Path(__file__).resolve().parent / "native" / "build" / "ntfs_usn_probe.exe"
MAX_RENDER_CHILDREN = 4000
ROOT_REFINE_MIN_SHARE = 0.005
ROOT_REFINE_MIN_TARGETS = 8
WINDOWS_SYSTEM_FILE_NAMES = {
    "pagefile.sys",
    "hiberfil.sys",
    "swapfile.sys",
    "dumpstack.log.tmp",
    "memory.dmp",
}
WINDOWS_SYSTEM_DIR_NAMES = {
    "windows",
    "system volume information",
    "$rmmetadata",
    "$recycle.bin",
    "config.msi",
    "recovery",
}


def _system_root_path() -> str:
    return os.path.normcase(os.path.normpath(os.environ.get("SystemRoot") or r"C:\Windows"))


def _system_drive_path() -> str:
    return os.path.normcase(os.path.dirname(_system_root_path()))


def _system_root_prefixes() -> tuple[str, ...]:
    drive_root = _system_drive_path()
    return (
        _system_root_path(),
        os.path.normcase(os.path.join(drive_root, "System Volume Information")),
        os.path.normcase(os.path.join(drive_root, "$Recycle.Bin")),
        os.path.normcase(os.path.join(drive_root, "$RmMetadata")),
        os.path.normcase(os.path.join(drive_root, "Recovery")),
    )


def _format_bytes(size: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    value = float(max(0, int(size or 0)))
    unit = units[0]
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            break
        value /= 1024.0
    return f"{value:.1f} {unit}"


def _append_treemap_profile(event: dict) -> None:
    try:
        TREEMAP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = dict(event or {})
        payload["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(TREEMAP_PROFILE_LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
    except Exception:
        pass


def _load_tree_cache_metadata(path: Path) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_tree_cache_metadata(path: Path, payload: dict) -> None:
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
    except Exception:
        pass


def _tree_cache_metadata_is_valid(metadata: dict | None, root_path: str, journal_state) -> bool:
    if not metadata or journal_state is None:
        return False
    try:
        return (
            int(metadata.get("cache_version") or 0) == TREEMAP_TREE_CACHE_VERSION
            and os.path.normcase(str(metadata.get("root_path") or "")) == os.path.normcase(root_path)
            and str(metadata.get("drive") or "") == str(journal_state.drive)
            and str(metadata.get("filesystem") or "") == str(journal_state.filesystem)
            and int(metadata.get("journal_id") or -1) == int(journal_state.journal_id)
            and int(metadata.get("next_usn") or -1) == int(journal_state.next_usn)
        )
    except Exception:
        return False


def _classify_special_kind(path: str, is_dir: bool) -> str:
    normalized = os.path.normcase(os.path.normpath(str(path or "")))
    name = os.path.basename(normalized).lower()
    if not name:
        return "node"
    for prefix in _system_root_prefixes():
        if normalized == prefix or normalized.startswith(prefix + os.sep):
            return "system"
    if not is_dir and name in WINDOWS_SYSTEM_FILE_NAMES:
        return "system"
    if is_dir and name in WINDOWS_SYSTEM_DIR_NAMES:
        return "system"
    return "node"


@dataclass
class TreeNode:
    name: str
    path: str
    size: int
    is_dir: bool
    children: list["TreeNode"] = field(default_factory=list)
    complete: bool = False
    kind: str = "node"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "size": int(self.size or 0),
            "is_dir": bool(self.is_dir),
            "complete": bool(self.complete),
            "kind": self.kind,
            "children": [child.to_dict() for child in self.children],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TreeNode":
        return cls(
            name=str(data.get("name") or ""),
            path=str(data.get("path") or ""),
            size=int(data.get("size") or 0),
            is_dir=bool(data.get("is_dir")),
            complete=bool(data.get("complete")),
            kind=str(data.get("kind") or "node"),
            children=[cls.from_dict(item) for item in list(data.get("children") or []) if isinstance(item, dict)],
        )


def _clone_tree(node: TreeNode | None) -> TreeNode | None:
    if node is None:
        return None
    return TreeNode.from_dict(node.to_dict())


def _build_tree_from_ntfs_stream(stream_path: str, root_path: str) -> TreeNode:
    root_name = os.path.basename(str(root_path or "").rstrip("\\/")) or str(root_path or "")
    root = TreeNode(
        name=root_name,
        path=str(root_path or ""),
        size=0,
        is_dir=True,
        children=[],
        complete=True,
        kind="ntfs_native_root",
    )
    nodes: dict[int, TreeNode] = {}
    parent_by_frn: dict[int, int] = {}
    ordered_frns: list[int] = []

    with open(stream_path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.rstrip("\r\n")
            if not line:
                continue
            parts = line.split("\t", 5)
            if len(parts) != 6:
                continue
            kind, raw_frn, raw_parent_frn, raw_is_dir, raw_size, raw_name = parts
            if kind != "R":
                continue
            frn = max(0, int(raw_frn or 0))
            parent_frn = max(0, int(raw_parent_frn or 0))
            is_dir = raw_is_dir == "1"
            name = str(raw_name or str(frn))
            nodes[frn] = TreeNode(
                name=name,
                path="",
                size=max(0, int(raw_size or 0)),
                is_dir=is_dir,
                children=[],
                complete=True,
                kind="node",
            )
            parent_by_frn[frn] = parent_frn
            ordered_frns.append(frn)

    for frn in ordered_frns:
        node = nodes.get(frn)
        if node is None:
            continue
        parent = nodes.get(parent_by_frn.get(frn, 0))
        if parent is None:
            root.children.append(node)
        else:
            parent.children.append(node)

    stack: list[tuple[TreeNode, bool]] = [(root, False)]
    while stack:
        node, visited = stack.pop()
        if visited:
            if node.is_dir:
                node.size = sum(int(child.size or 0) for child in node.children)
            continue
        stack.append((node, True))
        for child in node.children:
            stack.append((child, False))

    _hydrate_immediate_child_paths(root)
    return root


def _build_tree_from_ntfs_stream_binary(stream_path: str, root_path: str) -> TreeNode:
    root_name = os.path.basename(str(root_path or "").rstrip("\\/")) or str(root_path or "")
    root = TreeNode(
        name=root_name,
        path=str(root_path or ""),
        size=0,
        is_dir=True,
        children=[],
        complete=True,
        kind="ntfs_native_root",
    )
    nodes: dict[int, TreeNode] = {}
    parent_by_frn: dict[int, int] = {}
    ordered_frns: list[int] = []
    header_struct = struct.Struct("<8sI")
    record_struct = struct.Struct("<QQQI B")

    with open(stream_path, "rb") as handle:
        header = handle.read(header_struct.size)
        if len(header) != header_struct.size:
            raise RuntimeError("NTFS binary stream header missing or truncated.")
        magic, version = header_struct.unpack(header)
        if magic != b"MSNTFS01" or version != 1:
            raise RuntimeError("NTFS binary stream header is invalid.")

        while True:
            raw = handle.read(record_struct.size)
            if not raw:
                break
            if len(raw) != record_struct.size:
                raise RuntimeError("NTFS binary stream record header truncated.")
            frn, parent_frn, size, name_bytes, is_dir = record_struct.unpack(raw)
            name_raw = handle.read(name_bytes)
            if len(name_raw) != name_bytes:
                raise RuntimeError("NTFS binary stream record name truncated.")
            name = name_raw.decode("utf-8", errors="replace") or str(frn)
            nodes[frn] = TreeNode(
                name=name,
                path="",
                size=max(0, int(size or 0)),
                is_dir=bool(is_dir),
                children=[],
                complete=True,
                kind="node",
            )
            parent_by_frn[frn] = int(parent_frn)
            ordered_frns.append(int(frn))

    for frn in ordered_frns:
        node = nodes.get(frn)
        if node is None:
            continue
        parent = nodes.get(parent_by_frn.get(frn, 0))
        if parent is None:
            root.children.append(node)
        else:
            parent.children.append(node)

    stack: list[tuple[TreeNode, bool]] = [(root, False)]
    while stack:
        node, visited = stack.pop()
        if visited:
            if node.is_dir:
                node.size = sum(int(child.size or 0) for child in node.children)
            continue
        stack.append((node, True))
        for child in node.children:
            stack.append((child, False))

    _hydrate_immediate_child_paths(root)
    return root


def _build_tree_from_ntfs_tree_binary(stream_path: str, root_path: str, profile: dict[str, float] | None = None) -> TreeNode:
    root_name = os.path.basename(str(root_path or "").rstrip("\\/")) or str(root_path or "")
    root = TreeNode(
        name=root_name,
        path=str(root_path or ""),
        size=0,
        is_dir=True,
        children=[],
        complete=True,
        kind="ntfs_native_root",
    )
    header_struct = struct.Struct("<8sI")
    node_struct = struct.Struct("<QII B")
    decode_started = time.perf_counter()
    skipped_system_nodes = 0

    def _skip_tree_children(handle, child_count: int) -> int:
        skipped = 0
        remaining = [int(child_count)]
        while remaining:
            if remaining[-1] <= 0:
                remaining.pop()
                continue
            remaining[-1] -= 1
            raw_child = handle.read(node_struct.size)
            if len(raw_child) != node_struct.size:
                raise RuntimeError("NTFS tree binary node header truncated while skipping subtree.")
            _, nested_child_count, nested_name_bytes, _ = node_struct.unpack(raw_child)
            if nested_name_bytes:
                skipped_name = handle.read(nested_name_bytes)
                if len(skipped_name) != nested_name_bytes:
                    raise RuntimeError("NTFS tree binary node name truncated while skipping subtree.")
            skipped += 1
            if nested_child_count > 0:
                remaining.append(int(nested_child_count))
        return skipped

    with open(stream_path, "rb") as handle:
        header = handle.read(header_struct.size)
        if len(header) != header_struct.size:
            raise RuntimeError("NTFS tree binary header missing or truncated.")
        magic, version = header_struct.unpack(header)
        if magic != b"MSTREE01" or version != 1:
            raise RuntimeError("NTFS tree binary header is invalid.")

        raw_root = handle.read(node_struct.size)
        if len(raw_root) != node_struct.size:
            raise RuntimeError("NTFS tree binary root node missing.")
        _, root_child_count, root_name_bytes, root_is_dir = node_struct.unpack(raw_root)
        if root_name_bytes:
            skipped = handle.read(root_name_bytes)
            if len(skipped) != root_name_bytes:
                raise RuntimeError("NTFS tree binary root name truncated.")

        stack: list[tuple[TreeNode, int]] = [(root, int(root_child_count))]
        while stack:
            parent, remaining_children = stack[-1]
            if remaining_children <= 0:
                stack.pop()
                continue
            stack[-1] = (parent, remaining_children - 1)

            raw = handle.read(node_struct.size)
            if len(raw) != node_struct.size:
                raise RuntimeError("NTFS tree binary node header truncated.")
            size, child_count, name_bytes, is_dir = node_struct.unpack(raw)
            name_raw = handle.read(name_bytes)
            if len(name_raw) != name_bytes:
                raise RuntimeError("NTFS tree binary node name truncated.")
            name = name_raw.decode("utf-8", errors="replace") or ""
            child_path = os.path.join(parent.path, name) if parent.path else name
            child_kind = _classify_special_kind(child_path, bool(is_dir))
            child = TreeNode(
                name=name,
                path=child_path if parent is root else "",
                size=max(0, int(size or 0)),
                is_dir=bool(is_dir),
                children=[],
                complete=True,
                kind=child_kind,
            )
            parent.children.append(child)
            if parent is root and child.kind == "system" and child_count > 0:
                skipped_system_nodes += _skip_tree_children(handle, int(child_count))
                continue
            if child_count > 0:
                stack.append((child, int(child_count)))

    decode_s = time.perf_counter() - decode_started
    hydrate_started = time.perf_counter()
    _hydrate_immediate_child_paths(root)
    hydrate_s = time.perf_counter() - hydrate_started
    if profile is not None:
        profile["decode_s"] = decode_s
        profile["hydrate_s"] = hydrate_s
        profile["skipped_system_nodes"] = float(skipped_system_nodes)
    return root


def _build_tree_from_ntfs_tree_pipe(pipe, root_path: str) -> TreeNode:
    root_name = os.path.basename(str(root_path or "").rstrip("\\/")) or str(root_path or "")
    root = TreeNode(
        name=root_name,
        path=str(root_path or ""),
        size=0,
        is_dir=True,
        children=[],
        complete=True,
        kind="ntfs_native_root",
    )
    header_struct = struct.Struct("<8sI")
    node_struct = struct.Struct("<QII B")

    header = pipe.read(header_struct.size)
    if len(header) != header_struct.size:
        raise RuntimeError("NTFS tree stream header missing or truncated.")
    magic, version = header_struct.unpack(header)
    if magic != b"MSTREE01" or version != 1:
        raise RuntimeError("NTFS tree stream header is invalid.")

    raw_root = pipe.read(node_struct.size)
    if len(raw_root) != node_struct.size:
        raise RuntimeError("NTFS tree stream root node missing.")
    _, root_child_count, root_name_bytes, _ = node_struct.unpack(raw_root)
    if root_name_bytes:
        skipped = pipe.read(root_name_bytes)
        if len(skipped) != root_name_bytes:
            raise RuntimeError("NTFS tree stream root name truncated.")

    stack: list[tuple[TreeNode, int]] = [(root, int(root_child_count))]
    while stack:
        parent, remaining_children = stack[-1]
        if remaining_children <= 0:
            stack.pop()
            continue
        stack[-1] = (parent, remaining_children - 1)

        raw = pipe.read(node_struct.size)
        if len(raw) != node_struct.size:
            raise RuntimeError("NTFS tree stream node header truncated.")
        size, child_count, name_bytes, is_dir = node_struct.unpack(raw)
        name_raw = pipe.read(name_bytes)
        if len(name_raw) != name_bytes:
            raise RuntimeError("NTFS tree stream node name truncated.")
        child = TreeNode(
            name=name_raw.decode("utf-8", errors="replace") or "",
            path="",
            size=max(0, int(size or 0)),
            is_dir=bool(is_dir),
            children=[],
            complete=True,
            kind="node",
        )
        parent.children.append(child)
        if child_count > 0:
            stack.append((child, int(child_count)))

    _hydrate_immediate_child_paths(root)
    return root


def _build_tree_from_ntfs_stream_pipe(pipe, root_path: str) -> TreeNode:
    root_name = os.path.basename(str(root_path or "").rstrip("\\/")) or str(root_path or "")
    root = TreeNode(
        name=root_name,
        path=str(root_path or ""),
        size=0,
        is_dir=True,
        children=[],
        complete=True,
        kind="ntfs_native_root",
    )
    header_struct = struct.Struct("<8sI")
    record_struct = struct.Struct("<QQQI B")
    nodes: dict[int, TreeNode] = {}
    parent_by_frn: dict[int, int] = {}
    ordered_frns: list[int] = []

    header = pipe.read(header_struct.size)
    if len(header) != header_struct.size:
        raise RuntimeError("NTFS stream header missing or truncated.")
    magic, version = header_struct.unpack(header)
    if magic != b"MSNTFS01" or version != 1:
        raise RuntimeError("NTFS stream header is invalid.")

    while True:
        raw = pipe.read(record_struct.size)
        if not raw:
            break
        if len(raw) != record_struct.size:
            raise RuntimeError("NTFS stream record header truncated.")
        frn, parent_frn, size, name_bytes, is_dir = record_struct.unpack(raw)
        name_raw = pipe.read(name_bytes)
        if len(name_raw) != name_bytes:
            raise RuntimeError("NTFS stream record name truncated.")
        name = name_raw.decode("utf-8", errors="replace") or str(frn)
        nodes[frn] = TreeNode(
            name=name,
            path="",
            size=max(0, int(size or 0)),
            is_dir=bool(is_dir),
            children=[],
            complete=True,
            kind="node",
        )
        parent_by_frn[frn] = int(parent_frn)
        ordered_frns.append(int(frn))

    for frn in ordered_frns:
        node = nodes.get(frn)
        if node is None:
            continue
        parent = nodes.get(parent_by_frn.get(frn, 0))
        if parent is None:
            root.children.append(node)
        else:
            parent.children.append(node)

    stack: list[tuple[TreeNode, bool]] = [(root, False)]
    while stack:
        node, visited = stack.pop()
        if visited:
            if node.is_dir:
                node.size = sum(int(child.size or 0) for child in node.children)
            continue
        stack.append((node, True))
        for child in node.children:
            stack.append((child, False))

    _hydrate_immediate_child_paths(root)
    return root


def _hydrate_immediate_child_paths(node: TreeNode) -> None:
    for child in node.children:
        if child.kind in {"free_space", "protected", "pending_dir"}:
            continue
        if not child.path:
            child.path = os.path.join(node.path, child.name) if node.path else child.name
        child.kind = _classify_special_kind(child.path, child.is_dir)


def _hydrate_visible_paths(node: TreeNode) -> None:
    if not node.path:
        return
    _hydrate_immediate_child_paths(node)


def _sorted_children(children: list[TreeNode]) -> list[TreeNode]:
    return sorted(children, key=lambda item: int(item.size or 0), reverse=True)


def _normalize_sizes(values: list[int], area: float) -> list[float]:
    total = float(sum(max(0, v) for v in values))
    if total <= 0 or area <= 0:
        return [0.0 for _ in values]
    scale = area / total
    return [float(max(0, v)) * scale for v in values]


def _worst_ratio(row: list[float], short_side: float) -> float:
    if not row or short_side <= 0:
        return float("inf")
    total = sum(row)
    if total <= 0:
        return float("inf")
    max_item = max(row)
    min_item = min(row)
    if min_item <= 0:
        return float("inf")
    side_sq = short_side * short_side
    return max((side_sq * max_item) / (total * total), (total * total) / (side_sq * min_item))


def _layout_row(items: list[float], rect: QRectF) -> tuple[list[QRectF], QRectF]:
    total = sum(items)
    if total <= 0:
        return [], rect

    out: list[QRectF] = []
    if rect.width() >= rect.height():
        # Wider-than-tall: place a vertical column using the shortest side, per Bruls et al.
        row_width = total / rect.height() if rect.height() > 0 else 0.0
        cy = rect.y()
        for area in items:
            rh = area / row_width if row_width > 0 else 0.0
            out.append(QRectF(rect.x(), cy, row_width, rh))
            cy += rh
        remaining = QRectF(
            rect.x() + row_width,
            rect.y(),
            max(0.0, rect.width() - row_width),
            rect.height(),
        )
        return out, remaining

    # Taller-than-wide: place a horizontal row using the shortest side.
    row_height = total / rect.width() if rect.width() > 0 else 0.0
    cx = rect.x()
    for area in items:
        rw = area / row_height if row_height > 0 else 0.0
        out.append(QRectF(cx, rect.y(), rw, row_height))
        cx += rw
    remaining = QRectF(
        rect.x(),
        rect.y() + row_height,
        rect.width(),
        max(0.0, rect.height() - row_height),
    )
    return out, remaining


def _squarify_recursive(items: list[float], row: list[float], rect: QRectF, out: list[QRectF]) -> None:
    if not items:
        if row:
            laid_out, _ = _layout_row(row, rect)
            out.extend(laid_out)
        return

    candidate = items[0]
    short_side = min(rect.width(), rect.height())
    if not row or _worst_ratio(row, short_side) >= _worst_ratio(row + [candidate], short_side):
        _squarify_recursive(items[1:], row + [candidate], rect, out)
        return

    laid_out, remaining = _layout_row(row, rect)
    out.extend(laid_out)
    _squarify_recursive(items, [], remaining, out)


def squarify_rects(values: list[int], rect: QRectF) -> list[QRectF]:
    if not values:
        return []
    ordered_values = sorted([int(max(0, value)) for value in values], reverse=True)
    areas = _normalize_sizes(ordered_values, rect.width() * rect.height())
    out: list[QRectF] = []
    _squarify_recursive(areas, [], rect, out)
    return out


def layout_squarified_nodes(children: list[TreeNode], rect: QRectF) -> list[tuple[QRectF, TreeNode]]:
    visible_children = [child for child in children if int(child.size or 0) > 0]
    ordered_children = _sorted_children(visible_children)
    rects = squarify_rects([int(child.size or 0) for child in ordered_children], rect)
    return list(zip(rects, ordered_children))


def layout_root_nodes(children: list[TreeNode], rect: QRectF) -> list[tuple[QRectF, TreeNode]]:
    visible_children = [child for child in children if int(child.size or 0) > 0]
    if not visible_children:
        return []

    free_node = next((child for child in visible_children if child.kind == "free_space"), None)
    used_children = [child for child in visible_children if child.kind != "free_space"]
    if free_node is None or not used_children:
        return layout_squarified_nodes(visible_children, rect)

    total = sum(int(child.size or 0) for child in visible_children)
    free_size = int(free_node.size or 0)
    if total <= 0 or free_size <= 0:
        return layout_squarified_nodes(visible_children, rect)

    free_width = rect.width() * (free_size / total)
    free_width = max(1.0, min(rect.width() - 1.0, free_width))
    used_rect = QRectF(rect.x(), rect.y(), max(0.0, rect.width() - free_width), rect.height())
    free_rect = QRectF(rect.right() - free_width, rect.y(), free_width, rect.height())

    laid_out = layout_squarified_nodes(used_children, used_rect)
    laid_out.append((free_rect, free_node))
    return laid_out


def _frame_width(depth: int) -> float:
    base = 6.0
    factor = 0.72
    return max(1.0, base * (factor ** depth))


def _find_node_by_path(node: TreeNode | None, path: str) -> TreeNode | None:
    if node is None:
        return None
    normalized_target = os.path.normcase(os.path.normpath(path))
    if os.path.normcase(os.path.normpath(node.path or "")) == normalized_target:
        return node
    root_path = os.path.normcase(os.path.normpath(node.path or ""))
    if root_path and normalized_target.startswith(root_path):
        relative = os.path.relpath(path, node.path)
        if relative == ".":
            return node
        current = node
        for part in Path(relative).parts:
            _hydrate_immediate_child_paths(current)
            next_child = next((child for child in current.children if child.name == part), None)
            if next_child is None:
                return None
            current = next_child
        return current
    for child in node.children:
        found = _find_node_by_path(child, path)
        if found is not None:
            return found
    return None


def _explorer_target_for_node(node: TreeNode | None) -> str:
    if node is None or not node.path:
        return ""
    if node.is_dir:
        return node.path
    return os.path.dirname(node.path) or node.path


def _make_provisional_directory_node(path: str) -> TreeNode:
    name = os.path.basename(path.rstrip("\\/")) or path
    direct_file_bytes = 0
    direct_children = 0
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_symlink():
                        continue
                    direct_children += 1
                    if entry.is_file(follow_symlinks=False):
                        try:
                            direct_file_bytes += int(entry.stat(follow_symlinks=False).st_size or 0)
                        except (PermissionError, FileNotFoundError, OSError):
                            continue
                except Exception:
                    continue
    except (PermissionError, FileNotFoundError, OSError):
        pass

    provisional_size = max(1, direct_file_bytes, direct_children)
    return TreeNode(
        name=name,
        path=path,
        size=provisional_size,
        is_dir=True,
        children=[],
        complete=False,
        kind=_classify_special_kind(path, True),
    )


def _replace_child_node(children: list[TreeNode], replacement: TreeNode) -> list[TreeNode]:
    updated: list[TreeNode] = []
    replaced = False
    for child in children:
        if child.path == replacement.path:
            updated.append(replacement)
            replaced = True
        else:
            updated.append(child)
    if not replaced:
        updated.append(replacement)
    return updated


def _find_child_by_path(children: list[TreeNode], path: str) -> TreeNode | None:
    for child in children:
        if child.path == path:
            return child
    return None


def _replace_node_by_path(node: TreeNode, replacement: TreeNode) -> TreeNode:
    if node.path == replacement.path:
        return replacement
    if not node.children:
        return node
    updated_children = [_replace_node_by_path(child, replacement) for child in node.children]
    return TreeNode(
        name=node.name,
        path=node.path,
        size=node.size,
        is_dir=node.is_dir,
        children=updated_children,
        complete=node.complete,
        kind=node.kind,
    )


def _select_root_refine_targets(children: list[TreeNode], root_size: int) -> list[TreeNode]:
    directories = [
        child
        for child in _sorted_children(children)
        if child.is_dir and child.path and child.kind != "system"
    ]
    if not directories:
        return []

    if root_size <= 0:
        return directories[:ROOT_REFINE_MIN_TARGETS]

    selected: list[TreeNode] = []
    for child in directories:
        share = float(int(child.size or 0)) / float(root_size)
        if share >= ROOT_REFINE_MIN_SHARE or len(selected) < ROOT_REFINE_MIN_TARGETS:
            selected.append(child)
    return selected


def _is_special_users_path(path: str) -> bool:
    normalized = os.path.normcase(os.path.normpath(str(path or "")))
    return normalized.endswith(os.path.normcase(r"C:\Users"))


def _cap_children_for_render(node: TreeNode, *, max_children: int = MAX_RENDER_CHILDREN) -> TreeNode:
    if not node.children or len(node.children) <= max_children:
        return node

    ordered = _sorted_children(list(node.children))
    kept = ordered[:max_children]
    remainder = ordered[max_children:]
    remainder_size = sum(int(child.size or 0) for child in remainder)
    if remainder_size > 0:
        kept.append(
            TreeNode(
                name="Smaller items",
                path="",
                size=remainder_size,
                is_dir=False,
                children=[],
                complete=True,
                kind="protected",
            )
        )
    return TreeNode(
        name=node.name,
        path=node.path,
        size=node.size,
        is_dir=node.is_dir,
        children=kept,
        complete=node.complete,
        kind=node.kind,
    )


def _prepare_node_for_root_render(node: TreeNode, *, max_children: int = 96) -> TreeNode:
    if node.kind in {"free_space", "protected", "pending_dir"} or not node.children:
        return node
    if node.kind == "system":
        return TreeNode(
            name=node.name,
            path=node.path,
            size=int(node.size or 0),
            is_dir=node.is_dir,
            children=[],
            complete=node.complete,
            kind=node.kind,
        )

    ordered = _sorted_children(list(node.children))
    kept = ordered[:max_children]
    remainder = ordered[max_children:]
    prepared_children = [_prepare_node_for_root_render(child, max_children=max_children) for child in kept]
    remainder_size = sum(int(child.size or 0) for child in remainder)
    if remainder_size > 0:
        prepared_children.append(
            TreeNode(
                name="Smaller items",
                path="",
                size=remainder_size,
                is_dir=False,
                children=[],
                complete=True,
                kind="protected",
            )
        )
    return TreeNode(
        name=node.name,
        path=node.path,
        size=int(node.size or 0),
        is_dir=node.is_dir,
        children=prepared_children,
        complete=node.complete,
        kind=node.kind,
    )


def _summarize_root_view(node: TreeNode) -> TreeNode:
    if node.kind != "ntfs_native_root":
        return node
    _hydrate_immediate_child_paths(node)
    summarized_children = []
    for child in node.children:
        child_kind = _classify_special_kind(child.path, child.is_dir) if child.path else child.kind
        if child.kind != child_kind:
            child = TreeNode(
                name=child.name,
                path=child.path,
                size=int(child.size or 0),
                is_dir=child.is_dir,
                children=child.children,
                complete=child.complete,
                kind=child_kind,
            )
        summarized_children.append(_prepare_node_for_root_render(child))
    return TreeNode(
        name=node.name,
        path=node.path,
        size=int(node.size or 0),
        is_dir=node.is_dir,
        children=summarized_children,
        complete=node.complete,
        kind=node.kind,
    )


def _decorate_root_with_disk_regions(root_path: str, root_node: TreeNode) -> TreeNode:
    try:
        usage = shutil.disk_usage(root_path)
    except Exception:
        return root_node

    decorated_children = list(root_node.children)
    if hasattr(usage, "used"):
        actual_used = int(usage.used or 0)
        free_space = int(usage.free or 0)
    else:
        actual_used = int(usage[1] or 0)
        free_space = int(usage[2] or 0)

    pending_dirs = [child for child in decorated_children if child.kind == "pending_dir"]
    fixed_children = [child for child in decorated_children if child.kind != "pending_dir"]
    fixed_used = sum(int(child.size or 0) for child in fixed_children)

    if pending_dirs:
        pending_total = sum(max(1, int(child.size or 0)) for child in pending_dirs)
        remaining_used = max(0, actual_used - fixed_used)
        scaled_pending: list[TreeNode] = []
        assigned = 0
        for index, child in enumerate(pending_dirs):
            raw_size = max(1, int(child.size or 0))
            if index == len(pending_dirs) - 1:
                scaled_size = max(1, remaining_used - assigned)
            else:
                scaled_size = max(1, int(round(remaining_used * (raw_size / pending_total))))
                assigned += scaled_size
            scaled_pending.append(
                TreeNode(
                    name=child.name,
                    path=child.path,
                    size=scaled_size,
                    is_dir=child.is_dir,
                    children=child.children,
                    complete=child.complete,
                    kind=child.kind,
                )
            )
        decorated_children = fixed_children + scaled_pending
        hidden_used = 0
    else:
        accounted_used = sum(int(child.size or 0) for child in decorated_children)
        hidden_used = max(0, actual_used - accounted_used)

    # Native NTFS scans account for file records directly. Any remaining delta here is
    # not trustworthy enough to present as a giant "unscanned" block.
    if root_node.kind == "ntfs_native_root":
        hidden_used = 0

    if hidden_used > 0:
        decorated_children.append(
            TreeNode(
                name="Unscanned or protected",
                path="",
                size=hidden_used,
                is_dir=False,
                children=[],
                complete=True,
                kind="protected",
            )
        )
    if free_space > 0:
        decorated_children.append(
            TreeNode(
                name="Free space",
                path="",
                size=free_space,
                is_dir=False,
                children=[],
                complete=True,
                kind="free_space",
            )
        )

    return TreeNode(
        name=root_node.name,
        path=root_node.path,
        size=(sum(int(child.size or 0) for child in decorated_children) + free_space)
        if root_node.kind == "ntfs_native_root"
        else (actual_used + free_space),
        is_dir=True,
        children=_sorted_children(decorated_children),
        complete=root_node.complete,
        kind=root_node.kind,
    )


class DriveScanWorker(QThread):
    progress = Signal(str)
    partial = Signal(object)
    finished_scan = Signal(object)
    failed = Signal(str)
    summary = Signal(object)

    def __init__(self, root_path: str):
        super().__init__()
        self.root_path = str(root_path or "")
        self._started_at = 0.0

    def run(self) -> None:
        try:
            self._started_at = time.monotonic()
            root = self._scan_root(self.root_path)
            self.finished_scan.emit(root)
        except Exception as exc:
            self.failed.emit(str(exc))

    def _scan_root(self, root_path: str) -> TreeNode:
        native_root = self._scan_root_ntfs(root_path)
        if native_root is not None:
            return native_root

        name = os.path.basename(root_path.rstrip("\\/")) or root_path
        root = TreeNode(name=name, path=root_path, size=0, is_dir=True, children=[], complete=False)
        children: list[TreeNode] = []

        try:
            with os.scandir(root_path) as it:
                entries = list(it)
        except Exception as exc:
            raise RuntimeError(f"Unable to scan {root_path}: {exc}") from exc

        top_level_files: list[str] = []
        top_level_dirs: list[str] = []
        for entry in entries:
            if self.isInterruptionRequested():
                raise RuntimeError("Scan cancelled.")
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    top_level_dirs.append(entry.path)
                else:
                    top_level_files.append(entry.path)
            except Exception:
                continue

        for file_path in top_level_files:
            if self.isInterruptionRequested():
                raise RuntimeError("Scan cancelled.")
            self.progress.emit(file_path)
            child = self._scan_entry(file_path)
            if child.size <= 0:
                continue
            children.append(child)
            root.children = _sorted_children(list(children))
            root.size = sum(int(item.size or 0) for item in children)
            self.partial.emit(_clone_tree(root))

        for directory_path in top_level_dirs:
            if self.isInterruptionRequested():
                raise RuntimeError("Scan cancelled.")
            provisional = _make_provisional_directory_node(directory_path)
            children.append(provisional)

        if children:
            root.children = _sorted_children(list(children))
            root.size = sum(int(item.size or 0) for item in children)
            self.partial.emit(_clone_tree(root))
        self._emit_summary(0, len(top_level_dirs))

        max_workers = min(max(2, os.cpu_count() or 4), max(2, len(top_level_dirs), 8))
        completed_dirs = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for directory_path in top_level_dirs:
                self.progress.emit(directory_path)
                futures[executor.submit(self._scan_entry, directory_path)] = directory_path

            for future in as_completed(futures):
                if self.isInterruptionRequested():
                    raise RuntimeError("Scan cancelled.")
                child = future.result()
                children = _replace_child_node(children, child)
                root.children = _sorted_children(list(children))
                root.size = sum(int(item.size or 0) for item in children)
                self.partial.emit(_clone_tree(root))
                completed_dirs += 1
                self._emit_summary(completed_dirs, len(top_level_dirs))

        root.children = _sorted_children(children)
        root.size = sum(int(item.size or 0) for item in children)
        root.complete = True
        self._emit_summary(len(top_level_dirs), len(top_level_dirs))
        return root

    def _scan_root_ntfs(self, root_path: str) -> TreeNode | None:
        if os.name != "nt":
            return None
        drive_root = os.path.splitdrive(root_path)[0] + "\\"
        if not drive_root or os.path.normcase(drive_root) != os.path.normcase(root_path):
            return None
        if not NTFS_HELPER_EXE.exists():
            return None

        probe = probe_ntfs_enumerator(root_path)
        if probe.filesystem.upper() != "NTFS":
            return None

        self.progress.emit(f"Using NTFS native full scan for {root_path}")
        self.summary.emit(
            {
                "mode": "ntfs_native",
                "phase": "building_root",
                "message": "Scanning NTFS metadata...",
            }
        )

        TREEMAP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_key = hashlib.sha1(os.path.normcase(root_path).encode("utf-8", errors="replace")).hexdigest()[:16]
        tree_stream = TREEMAP_CACHE_DIR / f"{cache_key}.ntfs.full.treebin"
        tree_meta = TREEMAP_CACHE_DIR / f"{cache_key}.ntfs.full.meta.json"
        helper_elapsed_s: float | None = None
        helper_profile: dict = {}
        journal_state_before = query_ntfs_journal_state(root_path)
        cache_hit = False
        if tree_stream.exists() and tree_stream.stat().st_size > 0:
            cached_meta = _load_tree_cache_metadata(tree_meta)
            if _tree_cache_metadata_is_valid(cached_meta, root_path, journal_state_before):
                cache_hit = True

        if cache_hit:
            self.summary.emit(
                {
                    "mode": "ntfs_native",
                    "phase": "building_root",
                    "message": "Loading validated treemap cache...",
                }
            )
        else:
            command = [str(NTFS_HELPER_EXE), "scan-ntfs", root_path, str(tree_stream)]
            try:
                result = subprocess.run(
                    command,
                    cwd=str(Path(__file__).resolve().parent),
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=600,
                )
            except subprocess.TimeoutExpired:
                self.progress.emit(f"NTFS native scan timed out for {root_path}. Falling back to standard scan.")
                self.summary.emit(
                    {
                        "mode": "ntfs_native",
                        "phase": "building_root",
                        "message": "NTFS fast path unavailable. Falling back to standard scan...",
                    }
                )
                _append_treemap_profile(
                    {
                        "event": "treemap_ntfs_fallback",
                        "root_path": root_path,
                        "reason": "timeout",
                    }
                )
                return None

            helper_text = (result.stdout or "").strip()
            if result.returncode != 0:
                detail = helper_text or (result.stderr or "").strip()
                self.progress.emit(f"NTFS native scan failed for {root_path}. Falling back to standard scan.")
                self.summary.emit(
                    {
                        "mode": "ntfs_native",
                        "phase": "building_root",
                        "message": "NTFS fast path unavailable. Falling back to standard scan...",
                    }
                )
                _append_treemap_profile(
                    {
                        "event": "treemap_ntfs_fallback",
                        "root_path": root_path,
                        "reason": "helper_failed",
                        "detail": detail,
                    }
                )
                return None

            try:
                helper_profile = json.loads(helper_text or "{}")
                helper_elapsed_ms = int(helper_profile.get("elapsed_ms") or 0)
                if helper_elapsed_ms > 0:
                    helper_elapsed_s = helper_elapsed_ms / 1000.0
            except Exception:
                helper_profile = {}
                helper_elapsed_s = None

            journal_state_after = query_ntfs_journal_state(root_path)
            if journal_state_after is not None and tree_stream.exists() and tree_stream.stat().st_size > 0:
                _write_tree_cache_metadata(
                    tree_meta,
                    {
                        "cache_version": TREEMAP_TREE_CACHE_VERSION,
                        "root_path": root_path,
                        "drive": journal_state_after.drive,
                        "filesystem": journal_state_after.filesystem,
                        "journal_id": int(journal_state_after.journal_id),
                        "next_usn": int(journal_state_after.next_usn),
                        "tree_file": tree_stream.name,
                    },
                )

        self.summary.emit(
            {
                "mode": "ntfs_native",
                "phase": "root_ready",
                "message": "Building treemap...",
            }
        )
        build_profile: dict[str, float] = {}
        python_build_started = time.perf_counter()
        root = _build_tree_from_ntfs_tree_binary(str(tree_stream), root_path, build_profile)
        python_build_s = time.perf_counter() - python_build_started
        profile_event = {
            "event": "treemap_ntfs_ready",
            "root_path": root_path,
            "cache_hit": cache_hit,
            "native_scan_s": helper_elapsed_s,
            "python_build_s": python_build_s,
            "tree_file": str(tree_stream),
            "tree_file_size": tree_stream.stat().st_size if tree_stream.exists() else 0,
            "top_level_dirs": len([child for child in root.children if child.is_dir]),
            "top_level_items": len(root.children),
        }
        profile_event.update(helper_profile)
        profile_event.update(build_profile)
        _append_treemap_profile(profile_event)
        self.summary.emit(
            {
                "mode": "ntfs_native",
                "phase": "root_ready",
                "top_level_dirs": len([child for child in root.children if child.is_dir]),
                "top_level_items": len(root.children),
                "message": "Loaded validated treemap cache." if cache_hit else "Treemap ready.",
            }
        )
        return root

    def _refine_ntfs_root_children(self, root: TreeNode) -> None:
        refine_targets = _select_root_refine_targets(root.children, int(root.size or 0))
        if not refine_targets:
            return
        total = len(refine_targets)
        self.summary.emit(
            {
                "mode": "ntfs_native",
                "phase": "refining",
                "completed_dirs": 0,
                "total_dirs": total,
                "current_name": refine_targets[0].name if refine_targets else "",
                "message": f"Top-level map ready | Refining {total} large folders...",
                "deferred_dirs": max(0, len([child for child in root.children if child.is_dir and child.path]) - total),
            }
        )

        max_workers = min(max(2, os.cpu_count() or 4), max(2, total))
        completed = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for child in refine_targets:
                # Root refinement exists to make the initial drive view useful, not to
                # recursively crawl half the disk before the user can interact. Keep
                # this pass one level deep and defer deep recursion to drill-down.
                futures[executor.submit(self._scan_entry_one_level, child.path)] = child

            for future in as_completed(futures):
                if self.isInterruptionRequested():
                    raise RuntimeError("Scan cancelled.")
                refined = future.result()
                existing = _find_child_by_path(root.children, refined.path)
                if existing is not None:
                    # Keep the original coarse root footprint stable while refinement
                    # fills in the child internals. This prevents abrupt whole-root
                    # relayout jumps on every background completion.
                    refined.size = int(existing.size or 0)
                root.children = _sorted_children(_replace_child_node(root.children, refined))
                self.partial.emit(_clone_tree(root))
                completed += 1
                next_name = ""
                if completed < total:
                    try:
                        pending = [futures[f].name for f in futures if not f.done()]
                        next_name = pending[0] if pending else ""
                    except Exception:
                        next_name = ""
                self.summary.emit(
                    {
                        "mode": "ntfs_native",
                        "phase": "refining",
                        "completed_dirs": completed,
                        "total_dirs": total,
                        "current_name": next_name,
                        "message": f"Refining large folders... {completed}/{total}",
                        "deferred_dirs": max(0, len([child for child in root.children if child.is_dir and child.path]) - total),
                    }
                )

    def _emit_summary(self, completed_dirs: int, total_dirs: int) -> None:
        elapsed = max(0.001, time.monotonic() - self._started_at)
        percent = 100 if total_dirs <= 0 else int(round((completed_dirs / total_dirs) * 100))
        eta_seconds: int | None = None
        if completed_dirs > 0 and total_dirs > completed_dirs:
            rate = completed_dirs / elapsed
            if rate > 0:
                eta_seconds = int(round((total_dirs - completed_dirs) / rate))
        self.summary.emit(
            {
                "completed_dirs": int(completed_dirs),
                "total_dirs": int(total_dirs),
                "percent": int(percent),
                "eta_seconds": eta_seconds,
            }
        )

    def _scan_entry(self, path: str) -> TreeNode:
        if self.isInterruptionRequested():
            raise RuntimeError("Scan cancelled.")

        name = os.path.basename(path.rstrip("\\/")) or path
        try:
            is_dir = os.path.isdir(path)
        except Exception:
            is_dir = False

        if not is_dir:
            try:
                size = int(os.path.getsize(path) or 0)
            except Exception:
                size = 0
            return TreeNode(
                name=name,
                path=path,
                size=size,
                is_dir=False,
                children=[],
                complete=True,
                kind=_classify_special_kind(path, False),
            )

        children: list[TreeNode] = []
        try:
            with os.scandir(path) as it:
                for entry in it:
                    if self.isInterruptionRequested():
                        raise RuntimeError("Scan cancelled.")
                    try:
                        if entry.is_symlink():
                            continue
                    except Exception:
                        continue
                    child = self._scan_entry(entry.path)
                    if child.size > 0:
                        children.append(child)
        except (PermissionError, FileNotFoundError, OSError):
            return TreeNode(name=name, path=path, size=0, is_dir=True, children=[], complete=True)

        children = _sorted_children(children)
        return TreeNode(
            name=name,
            path=path,
            size=sum(int(child.size or 0) for child in children),
            is_dir=True,
            children=children,
            complete=True,
            kind=_classify_special_kind(path, True),
        )

    def _scan_entry_one_level(self, path: str) -> TreeNode:
        if self.isInterruptionRequested():
            raise RuntimeError("Scan cancelled.")

        name = os.path.basename(path.rstrip("\\/")) or path
        children: list[TreeNode] = []
        try:
            with os.scandir(path) as it:
                for entry in it:
                    if self.isInterruptionRequested():
                        raise RuntimeError("Scan cancelled.")
                    try:
                        if entry.is_symlink():
                            continue
                    except Exception:
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        children.append(_make_provisional_directory_node(entry.path))
                    else:
                        try:
                            size = int(entry.stat(follow_symlinks=False).st_size or 0)
                        except Exception:
                            size = 0
                        if size > 0:
                            children.append(
                                TreeNode(
                                    name=entry.name,
                                    path=entry.path,
                                    size=size,
                                    is_dir=False,
                                    children=[],
                                    complete=True,
                                )
                            )
        except (PermissionError, FileNotFoundError, OSError):
            return TreeNode(name=name, path=path, size=0, is_dir=True, children=[], complete=False)

        children = _sorted_children(children)
        return TreeNode(
            name=name,
            path=path,
            size=sum(int(child.size or 0) for child in children),
            is_dir=True,
            children=children,
            complete=True,
            kind=_classify_special_kind(path, True),
        )


class SubtreeScanWorker(QThread):
    finished_scan = Signal(object)
    failed = Signal(str)

    def __init__(self, target_path: str):
        super().__init__()
        self.target_path = str(target_path or "")

    def run(self) -> None:
        try:
            node = self._scan_entry_one_level(self.target_path)
            self.finished_scan.emit(node)
        except Exception as exc:
            self.failed.emit(str(exc))

    def _scan_entry_one_level(self, path: str) -> TreeNode:
        if self.isInterruptionRequested():
            raise RuntimeError("Scan cancelled.")

        name = os.path.basename(path.rstrip("\\/")) or path
        try:
            is_dir = os.path.isdir(path)
        except Exception:
            is_dir = False

        if not is_dir:
            try:
                size = int(os.path.getsize(path) or 0)
            except Exception:
                size = 0
            return TreeNode(
                name=name,
                path=path,
                size=size,
                is_dir=False,
                children=[],
                complete=True,
                kind=_classify_special_kind(path, False),
            )

        children: list[TreeNode] = []
        try:
            with os.scandir(path) as it:
                for entry in it:
                    if self.isInterruptionRequested():
                        raise RuntimeError("Scan cancelled.")
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            children.append(_make_provisional_directory_node(entry.path))
                        else:
                            try:
                                size = int(entry.stat(follow_symlinks=False).st_size or 0)
                            except Exception:
                                size = 0
                            if size > 0:
                                children.append(
                                    TreeNode(
                                        name=entry.name,
                                        path=entry.path,
                                        size=size,
                                        is_dir=False,
                                        children=[],
                                        complete=True,
                                        kind=_classify_special_kind(entry.path, False),
                                    )
                                )
                    except Exception:
                        continue
        except (PermissionError, FileNotFoundError, OSError):
            return TreeNode(name=name, path=path, size=0, is_dir=True, children=[], complete=False)

        children = _sorted_children(children)
        return TreeNode(
            name=name,
            path=path,
            size=sum(int(child.size or 0) for child in children),
            is_dir=True,
            children=children,
            complete=True,
            kind=_classify_special_kind(path, True),
        )


def _gray_gradient(node: TreeNode, depth: int, rect: QRectF) -> QLinearGradient:
    gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    if node.kind == "free_space":
        gradient.setColorAt(0.0, QColor("#46507a"))
        gradient.setColorAt(1.0, QColor("#0f1327"))
        return gradient
    if node.kind == "system":
        gradient.setColorAt(0.0, QColor("#4b7394"))
        gradient.setColorAt(0.55, QColor("#bcd8ee"))
        gradient.setColorAt(1.0, QColor("#29445b"))
        return gradient
    if node.kind == "protected":
        gradient.setColorAt(0.0, QColor("#6d1f1f"))
        gradient.setColorAt(1.0, QColor("#241010"))
        return gradient
    if node.kind == "pending_dir":
        gradient.setColorAt(0.0, QColor("#6a5b22"))
        gradient.setColorAt(0.55, QColor("#c7b06a"))
        gradient.setColorAt(1.0, QColor("#4b4018"))
        return gradient

    hue_seed = (sum(ord(ch) for ch in node.name) * 7 + depth * 29) % 360
    sat = 150 if depth <= 1 else 120
    val_dark = 60 if depth == 0 else 52
    val_mid = 180 if depth == 0 else 156
    val_light = 255 if depth == 0 else 220
    dark = QColor.fromHsv(hue_seed, sat, val_dark)
    mid = QColor.fromHsv(hue_seed, max(90, sat - 28), val_mid)
    light = QColor.fromHsv((hue_seed + 8) % 360, max(70, sat - 42), val_light)
    gradient.setColorAt(0.0, dark)
    gradient.setColorAt(0.55, mid)
    gradient.setColorAt(1.0, light)
    return gradient


def _frame_gradient(depth: int, rect: QRectF) -> QLinearGradient:
    gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    dark = max(16, 42 - (depth * 3))
    light = min(200, 96 + (depth * 6))
    gradient.setColorAt(0.0, QColor(dark, dark, dark, 235))
    gradient.setColorAt(0.55, QColor(light, light, light, 210))
    gradient.setColorAt(1.0, QColor(max(0, dark - 4), max(0, dark - 4), max(0, dark - 4), 235))
    return gradient


def _node_label_text(node: TreeNode) -> str:
    if not node.name:
        return ""
    if node.kind == "pending_dir":
        return f"{node.name}\nRefining..."
    return f"{node.name}\n{_format_bytes(node.size)}"


def _label_header_height(metrics: QFontMetrics, text: str, width: float) -> float:
    if not text or width <= 24.0:
        return 0.0
    bounded_width = max(40, int(width - 12.0))
    rect = metrics.boundingRect(0, 0, bounded_width, 140, Qt.TextWordWrap, text)
    return float(rect.height() + 10.0)


def _label_text_color(node: TreeNode) -> QColor:
    if node.kind == "system":
        return QColor("#ffffff")
    if node.kind == "free_space":
        return QColor("#f6f8ff")
    if node.kind == "protected":
        return QColor("#fff4f4")
    if node.kind == "pending_dir":
        return QColor("#fff8dc")
    return QColor("#f8fbff")


def _label_plate_color(depth: int) -> QColor:
    alpha = 132 if depth == 0 else 188
    return QColor(4, 7, 12, alpha)


def _label_shadow_color() -> QColor:
    return QColor(0, 0, 0, 230)


class TreeMapWidget(QWidget):
    selection_changed = Signal(object)
    drill_requested = Signal(object)

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(520)
        self.setMouseTracking(True)
        self._node: TreeNode | None = None
        self._items: list[tuple[QRectF, TreeNode]] = []
        self._selected: TreeNode | None = None

    def set_node(self, node: TreeNode | None) -> None:
        self._node = node
        self._items = []
        self._selected = None
        self.update()

    def selected_node(self) -> TreeNode | None:
        return self._selected

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        try:
            painter.fillRect(self.rect(), QColor("#08090d"))
            painter.setRenderHint(QPainter.TextAntialiasing, True)

            node = self._node
            if node is None or not node.children:
                painter.setPen(QColor("#d0d5dd"))
                painter.drawText(self.rect(), Qt.AlignCenter, "No treemap data yet.")
                return

            outer = QRectF(6.0, 6.0, max(0.0, self.width() - 12.0), max(0.0, self.height() - 12.0))
            self._items = []
            self._paint_children(painter, node.children, outer, depth=0, is_root=True)
        except Exception:
            self._items = []
        finally:
            if painter.isActive():
                painter.end()

    def _paint_children(self, painter: QPainter, children: list[TreeNode], rect: QRectF, depth: int, is_root: bool = False) -> None:
        if not children or rect.width() < 2.0 or rect.height() < 2.0:
            return

        layout = layout_root_nodes(children, rect) if is_root else layout_squarified_nodes(children, rect)
        for child_rect, child in layout:
            if child_rect.width() < MIN_RENDER_EDGE or child_rect.height() < MIN_RENDER_EDGE:
                continue
            self._paint_node(painter, child, child_rect, depth)

    def _paint_node(self, painter: QPainter, node: TreeNode, rect: QRectF, depth: int) -> None:
        self._items.append((rect, node))
        painter.setPen(QPen(QColor(255, 255, 255, 38), 0.5))
        painter.setBrush(_gray_gradient(node, depth, rect))
        painter.drawRect(rect)

        if node.is_dir and not node.complete and node.kind in {"node", "system"}:
            painter.setPen(QPen(QColor(255, 215, 128, 140), 1.0, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect.adjusted(1.0, 1.0, -1.0, -1.0))

        if self._selected is not None and self._selected.path == node.path and node.path:
            painter.setPen(QPen(QColor("#ff4d4f"), 1.5))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect.adjusted(0.75, 0.75, -0.75, -0.75))

        label_text = _node_label_text(node) if rect.width() >= 160.0 and rect.height() >= 48.0 else ""
        label_header = 0.0
        if label_text:
            label_header = _label_header_height(painter.fontMetrics(), label_text, rect.width())
            label_rect = rect.adjusted(4.0, 4.0, -4.0, -(rect.height() - min(rect.height() - 4.0, label_header + 4.0)))
            painter.setPen(Qt.NoPen)
            painter.setBrush(_label_plate_color(depth))
            painter.drawRect(label_rect)
            text_rect = rect.adjusted(6.0, 4.0, -6.0, -4.0)
            painter.setPen(_label_shadow_color())
            painter.drawText(
                text_rect.adjusted(1.0, 1.0, 1.0, 1.0),
                Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap,
                label_text,
            )
            painter.setPen(_label_text_color(node))
            painter.drawText(
                text_rect,
                Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap,
                label_text,
            )

        if node.children and rect.width() >= MIN_RECURSE_EDGE and rect.height() >= MIN_RECURSE_EDGE and node.kind == "node":
            border = _frame_width(depth)
            if rect.width() > (border * 2.0) and rect.height() > (border * 2.0):
                painter.setPen(Qt.NoPen)
                painter.setBrush(_frame_gradient(depth, rect))
                painter.drawRect(rect)
                inner = rect.adjusted(border, border, -border, -border)
            else:
                inner = rect.adjusted(1.0, 1.0, -1.0, -1.0)
            if label_header > 0.0 and inner.height() > label_header + 18.0:
                inner = inner.adjusted(0.0, label_header, 0.0, 0.0)
            self._paint_children(painter, node.children, inner, depth + 1, is_root=False)

    def mousePressEvent(self, event) -> None:
        pos = event.position()
        for rect, node in reversed(self._items):
            if rect.contains(pos):
                self._selected = node
                self.selection_changed.emit(node)
                self.update()
                return
        self._selected = None
        self.selection_changed.emit(None)
        self.update()

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        for rect, node in reversed(self._items):
            if rect.contains(pos):
                tooltip = node.path or ("Free space" if node.kind == "free_space" else node.name)
                if tooltip:
                    QToolTip.showText(event.globalPosition().toPoint(), tooltip, self)
                return
        QToolTip.hideText()

    def leaveEvent(self, _event) -> None:
        QToolTip.hideText()

    def mouseDoubleClickEvent(self, event) -> None:
        pos = event.position()
        for rect, node in reversed(self._items):
            if rect.contains(pos) and node.is_dir and node.path:
                self.drill_requested.emit(node)
                return


class TreemapDialog(QDialog):
    def __init__(self, root_path: str, parent=None):
        super().__init__(parent)
        self.root_path = str(root_path or "")
        self.setWindowTitle(f"Treemap - {self.root_path}")
        self.resize(1380, 900)
        self.worker: DriveScanWorker | None = None
        self.subtree_worker: SubtreeScanWorker | None = None
        self._closing = False
        self._root_node: TreeNode | None = None
        self._zoom_paths: list[str] = []
        self._last_summary: dict | None = None
        self._refresh_started_at = 0.0

        self.location_label = QLabel(self.root_path)
        self.location_label.setWordWrap(True)

        self.status_label = QLabel(f"Preparing treemap for {self.root_path}...")
        self.status_label.setWordWrap(True)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)

        self.treemap = TreeMapWidget()
        self.treemap.selection_changed.connect(self._update_selection)
        self.treemap.drill_requested.connect(self._drill_into_node)

        self.path_label = QLabel("Selected: none")
        self.path_label.setWordWrap(True)

        self.btn_up = QPushButton("Up One Level")
        self.btn_up.clicked.connect(self._navigate_up)
        self.btn_up.setEnabled(False)
        self.btn_up.setToolTip("Unavailable at the drive root.")
        self.btn_up.setStyleSheet(
            "QPushButton:disabled { color: #7f8794; background-color: #2a2e36; border-color: #4a5160; }"
        )
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self._start_refresh)
        self.btn_open = QPushButton("Open Selected in Explorer")
        self.btn_open.clicked.connect(self._open_selected)
        self.btn_open.setEnabled(False)
        self.btn_open.setToolTip("Select an item to open its location in Explorer.")
        self.btn_open.setStyleSheet(
            "QPushButton:disabled { color: #7f8794; background-color: #2a2e36; border-color: #4a5160; }"
        )
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.accept)

        button_row = QHBoxLayout()
        button_row.addWidget(self.btn_up)
        button_row.addWidget(self.btn_refresh)
        button_row.addStretch(1)
        button_row.addWidget(self.btn_open)
        button_row.addWidget(self.btn_close)

        layout = QVBoxLayout()
        layout.addWidget(self.location_label)
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress)
        layout.addWidget(self.treemap, 1)
        layout.addWidget(self.path_label)
        layout.addLayout(button_row)
        self.setLayout(layout)

        self._start_refresh()

    def _start_refresh(self) -> None:
        if self.worker is not None:
            try:
                if self.worker.isRunning():
                    self.worker.requestInterruption()
                    self.worker.wait(1000)
            except RuntimeError:
                pass
            self.worker = None

        self._refresh_started_at = time.monotonic()
        self._last_summary = None
        self.progress.setRange(0, 0)
        self.worker = DriveScanWorker(self.root_path)
        self.worker.progress.connect(self._on_progress)
        self.worker.partial.connect(self._on_partial)
        self.worker.finished_scan.connect(self._on_finished)
        self.worker.failed.connect(self._on_failed)
        self.worker.summary.connect(self._on_summary)
        self.worker.finished.connect(self._clear_worker)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()

    def _clear_worker(self) -> None:
        self.worker = None

    def _current_view_path(self) -> str:
        if self._zoom_paths:
            return self._zoom_paths[-1]
        return self.root_path

    def _visible_node(self) -> TreeNode | None:
        if self._root_node is None:
            return None
        view_path = self._current_view_path()
        base_node = _find_node_by_path(self._root_node, view_path) or self._root_node
        _hydrate_visible_paths(base_node)
        if base_node.path == self.root_path:
            return _summarize_root_view(_decorate_root_with_disk_regions(self.root_path, base_node))
        return base_node

    def _apply_current_view(self) -> None:
        if self._closing:
            return
        visible = self._visible_node()
        visible = _cap_children_for_render(visible) if visible is not None else None
        self.treemap.set_node(visible)
        if visible is None:
            self.location_label.setText(self.root_path)
        else:
            self.location_label.setText(visible.path or visible.name)
        can_go_up = bool(self._zoom_paths)
        self.btn_up.setEnabled(can_go_up)
        self.btn_up.setToolTip("Go up one level." if can_go_up else "Unavailable at the drive root.")

    def _on_progress(self, path: str) -> None:
        if self._closing:
            return
        if self._last_summary is None:
            self.status_label.setText(self._with_elapsed(f"Refreshing treemap: {path}"))

    def _on_partial(self, node: TreeNode) -> None:
        if self._closing:
            return
        self._root_node = node
        self._apply_current_view()

    def _on_finished(self, node: TreeNode) -> None:
        if self._closing:
            return
        self._root_node = node
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        if node.kind != "ntfs_native_root":
            self.status_label.setText(
                self._with_elapsed(self._format_summary_line(
                    len([c for c in node.children if c.is_dir]),
                    len([c for c in node.children if c.is_dir]),
                    100,
                    0,
                ))
            )
        self._apply_current_view()

    def _on_failed(self, message: str) -> None:
        if self._closing:
            return
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.status_label.setText(self._with_elapsed(f"Treemap refresh failed: {message}"))
        QMessageBox.warning(self, "Treemap Scan Failed", message)

    def _elapsed_seconds_text(self) -> str:
        if self._refresh_started_at <= 0:
            return "0.0s"
        return f"{max(0.0, time.monotonic() - self._refresh_started_at):.1f}s"

    def _with_elapsed(self, message: str) -> str:
        return f"{message} | Elapsed {self._elapsed_seconds_text()}"

    def _format_summary_line(self, completed_dirs: int, total_dirs: int, percent: int, eta_seconds: int | None) -> str:
        eta_text = "ETA estimating..."
        if eta_seconds is not None:
            minutes, seconds = divmod(max(0, int(eta_seconds)), 60)
            hours, minutes = divmod(minutes, 60)
            if hours > 0:
                eta_text = f"ETA {hours}h {minutes}m {seconds}s"
            elif minutes > 0:
                eta_text = f"ETA {minutes}m {seconds}s"
            else:
                eta_text = f"ETA {seconds}s"
        return f"Directories complete: {completed_dirs}/{total_dirs} | {percent}% complete | {eta_text}"

    def _on_summary(self, payload: dict) -> None:
        if self._closing:
            return
        self._last_summary = dict(payload or {})
        mode = str(self._last_summary.get("mode") or "")
        phase = str(self._last_summary.get("phase") or "")
        if mode == "ntfs_native":
            message = str(self._last_summary.get("message") or "").strip()
            if phase == "building_root":
                self.status_label.setText(self._with_elapsed(message or "Building top-level NTFS map..."))
                return
            if phase == "root_ready":
                top_level_dirs = int(self._last_summary.get("top_level_dirs") or 0)
                top_level_items = int(self._last_summary.get("top_level_items") or 0)
                self.status_label.setText(
                    self._with_elapsed(
                        message or f"Top-level map ready | {top_level_dirs} folders | {top_level_items} visible root items"
                    )
                )
                return
            if phase == "refining":
                completed_dirs = int(self._last_summary.get("completed_dirs") or 0)
                total_dirs = int(self._last_summary.get("total_dirs") or 0)
                current_name = str(self._last_summary.get("current_name") or "").strip()
                deferred_dirs = int(self._last_summary.get("deferred_dirs") or 0)
                if total_dirs > 0 and completed_dirs >= total_dirs:
                    suffix = (
                        f" {deferred_dirs} smaller folders remain coarse until drill-down."
                        if deferred_dirs > 0
                        else ""
                    )
                    self.status_label.setText(
                        self._with_elapsed(f"Top-level refinement complete.{suffix}")
                    )
                    return
                if current_name:
                    self.status_label.setText(
                        self._with_elapsed(
                            message
                            or f"Refining large folders... {completed_dirs}/{total_dirs} | Next: {current_name}"
                        )
                    )
                else:
                    suffix = f" | {deferred_dirs} smaller folders stay coarse until drill-down" if deferred_dirs > 0 else ""
                    self.status_label.setText(
                        self._with_elapsed(message or f"Refining large folders... {completed_dirs}/{total_dirs}{suffix}")
                    )
                return
        completed_dirs = int(self._last_summary.get("completed_dirs") or 0)
        total_dirs = int(self._last_summary.get("total_dirs") or 0)
        percent = int(self._last_summary.get("percent") or 0)
        eta_seconds = self._last_summary.get("eta_seconds")
        self.status_label.setText(self._with_elapsed(self._format_summary_line(completed_dirs, total_dirs, percent, eta_seconds)))

    def _update_selection(self, node: TreeNode | None) -> None:
        if self._closing:
            return
        if node is None:
            self.path_label.setText("Selected: none")
            self.btn_open.setEnabled(False)
            self.btn_open.setToolTip("Select an item to open its location in Explorer.")
            return
        label = node.path or node.name
        kind_label = ""
        if node.kind == "free_space":
            kind_label = " (free space)"
        elif node.kind == "protected":
            kind_label = " (unscanned or protected)"
        elif node.kind == "system":
            kind_label = " (system-managed)"
        elif node.kind == "pending_dir":
            kind_label = " (refining)"
        warning = ""
        if node.kind == "system":
            warning = " System-managed content. Avoid modifying these files unless you know exactly what you are doing."
        self.path_label.setText(f"Selected: {label}{kind_label} - {_format_bytes(node.size)}.{warning}".strip())
        can_open = bool(node.path) and node.kind != "system"
        self.btn_open.setEnabled(can_open)
        if node.kind == "system":
            self.btn_open.setToolTip("Unavailable for system-managed content.")
        elif node.path:
            self.btn_open.setToolTip("Open the selected item in Explorer.")
        else:
            self.btn_open.setToolTip("Select an item to open its location in Explorer.")

    def _drill_into_node(self, node: TreeNode) -> None:
        if not node.is_dir or not node.path or node.kind == "system":
            if node.kind == "system":
                self.status_label.setText(self._with_elapsed(f"{node.path} is system-managed. Showing its total size only."))
            return
        if not node.complete:
            self._start_subtree_refine(node.path)
        self._zoom_paths.append(node.path)
        self._apply_current_view()
        if node.complete:
            self.status_label.setText(self._with_elapsed(f"Viewing {node.path}"))
        else:
            self.status_label.setText(self._with_elapsed(f"Opening coarse view for {node.path} | Refining on demand..."))

    def _start_subtree_refine(self, path: str) -> None:
        if self.subtree_worker is not None:
            try:
                if self.subtree_worker.isRunning():
                    return
            except RuntimeError:
                self.subtree_worker = None
        self.subtree_worker = SubtreeScanWorker(path)
        self.subtree_worker.finished_scan.connect(self._on_subtree_finished)
        self.subtree_worker.failed.connect(self._on_subtree_failed)
        self.subtree_worker.finished.connect(self._clear_subtree_worker)
        self.subtree_worker.finished.connect(self.subtree_worker.deleteLater)
        self.subtree_worker.start()

    def _clear_subtree_worker(self) -> None:
        self.subtree_worker = None

    def _on_subtree_finished(self, node: TreeNode) -> None:
        if self._closing:
            return
        if self._root_node is None:
            return
        self._root_node = _replace_node_by_path(self._root_node, node)
        self._apply_current_view()
        self.status_label.setText(self._with_elapsed(f"Refined {node.path}"))

    def _on_subtree_failed(self, message: str) -> None:
        if self._closing:
            return
        self.status_label.setText(self._with_elapsed(f"On-demand refinement failed: {message}"))

    def _navigate_up(self) -> None:
        if not self._zoom_paths:
            return
        self._zoom_paths.pop()
        self._apply_current_view()

    def _open_selected(self) -> None:
        node = self.treemap.selected_node()
        if node is None or node.kind == "system":
            return
        target = _explorer_target_for_node(node)
        if not target:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(target))

    def closeEvent(self, event) -> None:
        self._closing = True
        try:
            if self.worker is not None and self.worker.isRunning():
                self.worker.requestInterruption()
                self.worker.wait(1000)
            if self.subtree_worker is not None and self.subtree_worker.isRunning():
                self.subtree_worker.requestInterruption()
                self.subtree_worker.wait(1000)
        except Exception:
            pass
        super().closeEvent(event)
