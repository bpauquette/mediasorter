from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass

from mediasorter_ntfs import probe_ntfs_enumerator


@dataclass(frozen=True)
class EnumerationBenchmark:
    backend_id: str
    target: str
    ok: bool
    elapsed_seconds: float
    directories: int = 0
    notes: str = ""


def benchmark_scandir_directory_count(root_path: str, *, parallel_top_level: bool = True, max_workers: int = 8) -> EnumerationBenchmark:
    start = time.perf_counter()
    try:
        if parallel_top_level:
            count = _count_dirs_parallel(root_path, max_workers=max_workers)
            backend_id = "scandir_parallel"
        else:
            count = _count_dirs_serial(root_path)
            backend_id = "scandir_serial"
        elapsed = time.perf_counter() - start
        return EnumerationBenchmark(
            backend_id=backend_id,
            target=root_path,
            ok=True,
            elapsed_seconds=elapsed,
            directories=int(count),
        )
    except Exception as exc:
        elapsed = time.perf_counter() - start
        return EnumerationBenchmark(
            backend_id="scandir_parallel" if parallel_top_level else "scandir_serial",
            target=root_path,
            ok=False,
            elapsed_seconds=elapsed,
            notes=str(exc),
        )


def benchmark_ntfs_probe(root_path: str) -> EnumerationBenchmark:
    start = time.perf_counter()
    probe = probe_ntfs_enumerator(root_path)
    elapsed = time.perf_counter() - start
    ok = bool(probe.volume_open_ok and probe.query_journal_ok and probe.enum_usn_ok)
    notes = probe.notes or ""
    if not ok:
        notes = notes or f"open_error={probe.open_error} query_error={probe.query_error} enum_error={probe.enum_error}"
    return EnumerationBenchmark(
        backend_id="ntfs_mft_probe",
        target=root_path,
        ok=ok,
        elapsed_seconds=elapsed,
        directories=0,
        notes=notes,
    )


def benchmark_report(root_path: str) -> dict:
    serial = benchmark_scandir_directory_count(root_path, parallel_top_level=False)
    parallel = benchmark_scandir_directory_count(root_path, parallel_top_level=True)
    ntfs = benchmark_ntfs_probe(root_path)
    return {
        "target": root_path,
        "serial": asdict(serial),
        "parallel": asdict(parallel),
        "ntfs_probe": asdict(ntfs),
    }


def benchmark_report_json(root_path: str) -> str:
    return json.dumps(benchmark_report(root_path), indent=2)


def _count_dirs_serial(root_path: str) -> int:
    count = 0
    stack = [root_path]
    while stack:
        path = stack.pop()
        try:
            with os.scandir(path) as it:
                for entry in it:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            count += 1
                            stack.append(entry.path)
                    except Exception:
                        continue
        except Exception:
            continue
    return int(count)


def _count_subtree_dirs(start_path: str) -> int:
    count = 1
    stack = [start_path]
    while stack:
        path = stack.pop()
        try:
            with os.scandir(path) as it:
                for entry in it:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            count += 1
                            stack.append(entry.path)
                    except Exception:
                        continue
        except Exception:
            continue
    return int(count)


def _count_dirs_parallel(root_path: str, *, max_workers: int = 8) -> int:
    try:
        with os.scandir(root_path) as it:
            top_dirs = []
            for entry in it:
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        top_dirs.append(entry.path)
                except Exception:
                    continue
    except Exception:
        return 0

    total = 0
    worker_count = min(max(2, max_workers), max(2, len(top_dirs), 2))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(_count_subtree_dirs, path) for path in top_dirs]
        for future in as_completed(futures):
            total += int(future.result() or 0)
    return int(total)
