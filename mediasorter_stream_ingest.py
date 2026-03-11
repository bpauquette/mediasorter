from __future__ import annotations

from dataclasses import dataclass
from pathlib import PureWindowsPath

from mediasorter_metadata_tree import FilesystemMetadataRecord


def parse_win32_stream(stream_path: str, *, root_path: str) -> list[FilesystemMetadataRecord]:
    records: list[FilesystemMetadataRecord] = []
    frn_by_path: dict[str, int] = {str(PureWindowsPath(root_path)): 0}
    next_frn = 1

    with open(stream_path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.rstrip("\r\n")
            if not line:
                continue
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue
            kind, raw_size, raw_path = parts
            full_path = str(PureWindowsPath(raw_path))
            parent_path = str(PureWindowsPath(full_path).parent)
            if parent_path == full_path:
                parent_path = str(PureWindowsPath(root_path))
            if parent_path not in frn_by_path:
                frn_by_path[parent_path] = next_frn
                next_frn += 1
            frn = next_frn
            next_frn += 1
            frn_by_path[full_path] = frn
            records.append(
                FilesystemMetadataRecord(
                    frn=frn,
                    parent_frn=frn_by_path[parent_path],
                    name=PureWindowsPath(full_path).name,
                    is_dir=(kind == "D"),
                    size=max(0, int(raw_size or 0)),
                )
            )

    return records


def parse_ntfs_stream(stream_path: str) -> list[FilesystemMetadataRecord]:
    records: list[FilesystemMetadataRecord] = []

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
            records.append(
                FilesystemMetadataRecord(
                    frn=max(0, int(raw_frn or 0)),
                    parent_frn=max(0, int(raw_parent_frn or 0)),
                    name=str(raw_name or ""),
                    is_dir=(raw_is_dir == "1"),
                    size=max(0, int(raw_size or 0)),
                )
            )

    return records


@dataclass
class _NtfsFlatRecord:
    frn: int
    parent_frn: int
    name: str
    is_dir: bool
    size: int


@dataclass
class NtfsRootSummaryRecord:
    name: str
    path: str
    size: int
    is_dir: bool


def summarize_ntfs_root_stream(stream_path: str, *, root_path: str) -> tuple[str, int, list[NtfsRootSummaryRecord]]:
    records_by_frn: dict[int, _NtfsFlatRecord] = {}
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
            records_by_frn[frn] = _NtfsFlatRecord(
                frn=frn,
                parent_frn=parent_frn,
                name=str(raw_name or ""),
                is_dir=(raw_is_dir == "1"),
                size=max(0, int(raw_size or 0)),
            )
            ordered_frns.append(frn)

    subtree_sizes: dict[int, int] = {frn: record.size for frn, record in records_by_frn.items()}
    for frn in reversed(ordered_frns):
        record = records_by_frn.get(frn)
        if record is None:
            continue
        parent_frn = int(record.parent_frn or 0)
        if parent_frn in subtree_sizes:
            subtree_sizes[parent_frn] = subtree_sizes.get(parent_frn, 0) + subtree_sizes.get(frn, 0)

    root_children: list[NtfsRootSummaryRecord] = []
    root_size = 0
    for frn in ordered_frns:
        record = records_by_frn.get(frn)
        if record is None:
            continue
        if record.parent_frn in records_by_frn:
            continue
        size = max(0, int(subtree_sizes.get(frn, record.size)))
        root_children.append(
            NtfsRootSummaryRecord(
                name=record.name or str(frn),
                path=str(PureWindowsPath(root_path) / (record.name or str(frn))),
                size=size,
                is_dir=record.is_dir,
            )
        )
        root_size += size

    root_children.sort(key=lambda child: int(child.size or 0), reverse=True)
    return str(PureWindowsPath(root_path).name or root_path), root_size, root_children


def parse_ntfs_root_summary(stream_path: str, *, root_path: str) -> tuple[str, int, list[NtfsRootSummaryRecord]]:
    root_children: list[NtfsRootSummaryRecord] = []
    root_size = 0

    with open(stream_path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.rstrip("\r\n")
            if not line:
                continue
            parts = line.split("\t", 3)
            if len(parts) != 4:
                continue
            kind, raw_is_dir, raw_size, raw_name = parts
            if kind != "S":
                continue
            size = max(0, int(raw_size or 0))
            root_children.append(
                NtfsRootSummaryRecord(
                    name=str(raw_name or ""),
                    path=str(PureWindowsPath(root_path) / (raw_name or "")),
                    size=size,
                    is_dir=(raw_is_dir == "1"),
                )
            )
            root_size += size

    root_children.sort(key=lambda child: int(child.size or 0), reverse=True)
    return str(PureWindowsPath(root_path).name or root_path), root_size, root_children
