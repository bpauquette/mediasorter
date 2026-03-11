from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from mediasorter_platform_enum import _get_windows_filesystem_name, _query_usn_journal_available


GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_READ_ATTRIBUTES = 0x00000080
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FSCTL_QUERY_USN_JOURNAL = 0x000900F4
FSCTL_ENUM_USN_DATA = 0x000900B3


class USN_JOURNAL_DATA_V0(ctypes.Structure):
    _fields_ = [
        ("UsnJournalID", ctypes.c_longlong),
        ("FirstUsn", ctypes.c_longlong),
        ("NextUsn", ctypes.c_longlong),
        ("LowestValidUsn", ctypes.c_longlong),
        ("MaxUsn", ctypes.c_longlong),
        ("MaximumSize", ctypes.c_longlong),
        ("AllocationDelta", ctypes.c_longlong),
    ]


class MFT_ENUM_DATA_V0(ctypes.Structure):
    _fields_ = [
        ("StartFileReferenceNumber", ctypes.c_longlong),
        ("LowUsn", ctypes.c_longlong),
        ("HighUsn", ctypes.c_longlong),
    ]


@dataclass(frozen=True)
class NTFSEnumerationProbe:
    drive: str
    filesystem: str
    journal_present: bool
    volume_open_ok: bool
    query_journal_ok: bool
    enum_usn_ok: bool
    open_error: int = 0
    query_error: int = 0
    enum_error: int = 0
    notes: str = ""


@dataclass(frozen=True)
class NTFSJournalState:
    drive: str
    filesystem: str
    journal_id: int
    next_usn: int
    query_error: int = 0
    notes: str = ""


def probe_ntfs_enumerator(root_path: str) -> NTFSEnumerationProbe:
    drive = Path(root_path).anchor or str(root_path or "")
    filesystem = _get_windows_filesystem_name(drive)
    journal_present = _query_usn_journal_available(drive)
    if filesystem.upper() != "NTFS":
        return NTFSEnumerationProbe(
            drive=drive,
            filesystem=filesystem,
            journal_present=journal_present,
            volume_open_ok=False,
            query_journal_ok=False,
            enum_usn_ok=False,
            notes="Drive is not NTFS.",
        )

    handle, open_error = _open_volume_handle(drive)
    if handle is None:
        return NTFSEnumerationProbe(
            drive=drive,
            filesystem=filesystem,
            journal_present=journal_present,
            volume_open_ok=False,
            query_journal_ok=False,
            enum_usn_ok=False,
            open_error=open_error,
            notes="CreateFile on the raw volume failed for the current user context.",
        )

    try:
        query_journal_ok, query_error = _query_usn_journal(handle)
        enum_usn_ok, enum_error = _enum_usn_once(handle)
    finally:
        _close_handle(handle)

    notes = ""
    if not query_journal_ok or not enum_usn_ok:
        notes = "Raw NTFS journal enumeration is not yet available in this user context."

    return NTFSEnumerationProbe(
        drive=drive,
        filesystem=filesystem,
        journal_present=journal_present,
        volume_open_ok=True,
        query_journal_ok=query_journal_ok,
        enum_usn_ok=enum_usn_ok,
        query_error=query_error,
        enum_error=enum_error,
        notes=notes,
    )


def query_ntfs_journal_state(root_path: str) -> NTFSJournalState | None:
    drive = Path(root_path).anchor or str(root_path or "")
    filesystem = _get_windows_filesystem_name(drive)
    if filesystem.upper() != "NTFS":
        return None

    handle, open_error = _open_volume_handle(drive)
    if handle is None:
        return None
    try:
        journal_data, query_error = _query_usn_journal_data(handle)
    finally:
        _close_handle(handle)

    if journal_data is None:
        return None
    return NTFSJournalState(
        drive=drive,
        filesystem=filesystem,
        journal_id=int(journal_data.UsnJournalID),
        next_usn=int(journal_data.NextUsn),
        query_error=query_error,
        notes="" if query_error == 0 else "Failed to query NTFS journal state.",
    )


def _kernel32():
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _open_volume_handle(drive: str) -> tuple[int | None, int]:
    kernel32 = _kernel32()
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    invalid_handle = wintypes.HANDLE(-1).value
    path = rf"\\.\{drive.rstrip('\\')}"
    handle = create_file(
        path,
        FILE_READ_ATTRIBUTES,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        0,
        None,
    )
    error = ctypes.get_last_error()
    if handle == invalid_handle:
        return None, int(error or 0)
    return int(handle), 0


def _close_handle(handle: int) -> None:
    kernel32 = _kernel32()
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    close_handle(handle)


def _query_usn_journal(handle: int) -> tuple[bool, int]:
    journal_data, error = _query_usn_journal_data(handle)
    return journal_data is not None, error


def _query_usn_journal_data(handle: int) -> tuple[USN_JOURNAL_DATA_V0 | None, int]:
    kernel32 = _kernel32()
    device_io = kernel32.DeviceIoControl
    device_io.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    device_io.restype = wintypes.BOOL

    out_data = USN_JOURNAL_DATA_V0()
    returned = wintypes.DWORD(0)
    ok = device_io(
        handle,
        FSCTL_QUERY_USN_JOURNAL,
        None,
        0,
        ctypes.byref(out_data),
        ctypes.sizeof(out_data),
        ctypes.byref(returned),
        None,
    )
    error = ctypes.get_last_error()
    if not ok:
        return None, int(error or 0)
    return out_data, 0


def _enum_usn_once(handle: int) -> tuple[bool, int]:
    kernel32 = _kernel32()
    device_io = kernel32.DeviceIoControl
    device_io.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    device_io.restype = wintypes.BOOL

    med = MFT_ENUM_DATA_V0(0, 0, 0x7FFFFFFFFFFFFFFF)
    outbuf = ctypes.create_string_buffer(65536)
    returned = wintypes.DWORD(0)
    ok = device_io(
        handle,
        FSCTL_ENUM_USN_DATA,
        ctypes.byref(med),
        ctypes.sizeof(med),
        outbuf,
        ctypes.sizeof(outbuf),
        ctypes.byref(returned),
        None,
    )
    error = ctypes.get_last_error()
    return bool(ok), int(error or 0)
