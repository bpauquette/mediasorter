import ctypes
import subprocess
import sys

from mediasorter_cli import main


def _is_windows_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _quote_windows_arg(arg: str) -> str:
    return subprocess.list2cmdline([arg])


def _relaunch_as_admin() -> int:
    exe = sys.executable
    params = " ".join(_quote_windows_arg(arg) for arg in sys.argv)
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    if rc <= 32:
        raise RuntimeError(f"ShellExecuteW failed with code {rc}")
    return 0


def _ensure_windows_elevation() -> int | None:
    if sys.platform != "win32":
        return None
    if _is_windows_admin():
        return None
    return _relaunch_as_admin()


if __name__ == "__main__":
    elevate_rc = _ensure_windows_elevation()
    if elevate_rc is not None:
        raise SystemExit(elevate_rc)
    raise SystemExit(main())

