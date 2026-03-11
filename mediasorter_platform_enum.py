from __future__ import annotations

import platform
import subprocess
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EnumerationStrategy:
    platform_id: str
    backend_id: str
    label: str
    initial_scan: str
    refresh: str
    available: bool
    notes: str = ""


def detect_enumeration_strategy(root_path: str) -> EnumerationStrategy:
    system = platform.system().lower()
    if system == "windows":
        return _detect_windows_strategy(root_path)
    if system == "linux":
        return EnumerationStrategy(
            platform_id="linux",
            backend_id="linux_scandir_inotify",
            label="Linux native directory enumeration",
            initial_scan="Use low-level directory iteration (scandir/getdents-style) for the first crawl.",
            refresh="Use inotify/fanotify for incremental refresh where available.",
            available=True,
            notes="Fastest practical cross-filesystem path on Linux without filesystem-specific metadata readers.",
        )
    if system == "darwin":
        return EnumerationStrategy(
            platform_id="macos",
            backend_id="macos_bulkattr_fsevents",
            label="macOS bulk attribute enumeration",
            initial_scan="Use Darwin bulk directory attribute APIs for the first crawl.",
            refresh="Use FSEvents for incremental refresh.",
            available=True,
            notes="Best native path on macOS/APFS; keep a scandir fallback for portability.",
        )
    return EnumerationStrategy(
        platform_id=system or "unknown",
        backend_id="portable_scandir",
        label="Portable scandir fallback",
        initial_scan="Use recursive scandir traversal.",
        refresh="Rescan subtrees as needed.",
        available=True,
        notes="Fallback when no platform-specific fast path is available.",
    )


def _detect_windows_strategy(root_path: str) -> EnumerationStrategy:
    drive_root = Path(root_path).anchor or str(root_path or "")
    fs_name = _get_windows_filesystem_name(drive_root)
    if fs_name.upper() != "NTFS":
        return EnumerationStrategy(
            platform_id="windows",
            backend_id="windows_scandir_fallback",
            label="Windows fallback enumeration",
            initial_scan="Use recursive scandir traversal.",
            refresh="Rescan subtrees as needed.",
            available=True,
            notes=f"{drive_root} is not NTFS, so MFT/USN enumeration does not apply.",
        )

    journal_ok = _query_usn_journal_available(drive_root)
    if journal_ok:
        return EnumerationStrategy(
            platform_id="windows",
            backend_id="windows_ntfs_mft_usn",
            label="Windows NTFS MFT/USN strategy",
            initial_scan="Prefer NTFS MFT/USN enumeration for initial crawl.",
            refresh="Prefer USN journal deltas for refresh.",
            available=True,
            notes="Journal is present. Raw DeviceIoControl enumeration still needs implementation/probing in this app.",
        )

    return EnumerationStrategy(
        platform_id="windows",
        backend_id="windows_ntfs_scandir_fallback",
        label="Windows NTFS fallback enumeration",
        initial_scan="Use recursive scandir traversal.",
        refresh="Rescan subtrees as needed.",
        available=True,
        notes="Drive is NTFS, but USN journal probing failed in the current user context.",
    )


def _get_windows_filesystem_name(root_path: str) -> str:
    fs_name = _get_windows_filesystem_name_ctypes(root_path)
    if fs_name:
        return fs_name
    return _get_windows_filesystem_name_fsutil(root_path)


def _get_windows_filesystem_name_ctypes(root_path: str) -> str:
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_volume_information = kernel32.GetVolumeInformationW
        get_volume_information.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPWSTR,
            wintypes.DWORD,
        ]
        get_volume_information.restype = wintypes.BOOL
        serial = wintypes.DWORD()
        max_component = wintypes.DWORD()
        flags = wintypes.DWORD()
        name_buf = ctypes.create_unicode_buffer(260)
        fs_buf = ctypes.create_unicode_buffer(260)
        ok = get_volume_information(
            str(root_path),
            name_buf,
            260,
            ctypes.byref(serial),
            ctypes.byref(max_component),
            ctypes.byref(flags),
            fs_buf,
            260,
        )
        if ok:
            return fs_buf.value
    except Exception:
        pass
    return ""


def _get_windows_filesystem_name_fsutil(root_path: str) -> str:
    try:
        completed = subprocess.run(
            ["fsutil", "fsinfo", "volumeinfo", root_path],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return ""

    output = (completed.stdout or "") + "\n" + (completed.stderr or "")
    for line in output.splitlines():
        if "File System Name" in line:
            return line.split(":", 1)[-1].strip()
    return ""


def _query_usn_journal_available(root_path: str) -> bool:
    try:
        completed = subprocess.run(
            ["fsutil", "usn", "queryjournal", root_path],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return False
    return completed.returncode == 0 and "Usn Journal ID" in (completed.stdout or "")
