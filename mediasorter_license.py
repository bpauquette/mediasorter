import hashlib
import json
import os
import platform
import socket
import sys
import threading
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QInputDialog, QMessageBox

import mediasorter_core as core


LICENSE_STATE_FILE = Path(core.DATA_DIR) / "license_state.json"
DEFAULT_LICENSE_API_URL = ""
APP_VERSION = (os.environ.get("MEDIASORTER_APP_VERSION") or "1.0.0").strip()
LICENSE_HEARTBEAT_SECONDS = max(60, int(os.environ.get("MEDIASORTER_LICENSE_HEARTBEAT_SECONDS", "300") or "300"))
OFFLINE_GRACE_HOURS = max(1, int(os.environ.get("MEDIASORTER_LICENSE_OFFLINE_GRACE_HOURS", "72") or "72"))


@dataclass
class LicenseCheckResult:
    valid: bool
    message: str
    used_offline_grace: bool = False
    shutdown_requested: bool = False


def _read_runtime_text_file(filename: str) -> str:
    candidates = []
    try:
        candidates.append(Path(os.path.realpath(getattr(os, "_MEIPASS", ""))) / filename)
    except Exception:
        pass
    try:
        candidates.append(Path(sys.executable).resolve().parent / filename)
    except Exception:
        pass
    try:
        candidates.append(Path(__file__).resolve().parent / filename)
    except Exception:
        pass

    seen = set()
    for candidate in candidates:
        try:
            resolved = str(candidate.resolve()).lower()
            if resolved in seen or not candidate.exists():
                continue
            seen.add(resolved)
            content = (candidate.read_text(encoding="utf-8", errors="ignore") or "").strip()
            if content:
                return content.splitlines()[0].strip()
        except Exception:
            continue
    return ""


def license_api_url() -> str:
    return (
        os.environ.get("MEDIASORTER_LICENSE_API_URL")
        or _read_runtime_text_file("license_api_url.txt")
        or DEFAULT_LICENSE_API_URL
    ).strip().rstrip("/")


def license_api_enabled() -> bool:
    return bool(license_api_url())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _machine_guid() -> str:
    if os.name != "nt" or core.winreg is None:
        return ""
    try:
        with core.winreg.OpenKey(core.winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
            value, _ = core.winreg.QueryValueEx(key, "MachineGuid")
            return str(value or "")
    except Exception:
        return ""


def machine_fingerprint() -> str:
    parts = [
        socket.gethostname(),
        platform.system(),
        platform.release(),
        platform.machine(),
        str(uuid.getnode()),
        _machine_guid(),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8", errors="ignore")).hexdigest()


def _default_state() -> dict:
    return {
        "licenseKey": "",
        "machineFingerprint": machine_fingerprint(),
        "deviceName": socket.gethostname() or "Windows PC",
        "appVersion": APP_VERSION,
        "activationId": None,
        "lastValidatedAt": None,
        "offlineGraceUntil": None,
        "shutdownRequested": False,
        "shutdownReason": None,
    }


def load_state() -> dict:
    state = _default_state()
    if LICENSE_STATE_FILE.exists():
        try:
            parsed = json.loads(LICENSE_STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                state.update(parsed)
        except Exception:
            pass
    state["machineFingerprint"] = machine_fingerprint()
    state["deviceName"] = str(state.get("deviceName") or socket.gethostname() or "Windows PC")
    state["appVersion"] = APP_VERSION
    return state


def save_state(state: dict) -> None:
    LICENSE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(_default_state())
    payload.update(state or {})
    LICENSE_STATE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _post_json(path: str, payload: dict) -> dict:
    base_url = license_api_url()
    if not base_url:
        raise RuntimeError("License API URL is not configured for this build.")
    req = urllib.request.Request(
        url=f"{base_url}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            body = response.read().decode("utf-8")
            return json.loads(body or "{}")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            body = exc.read().decode("utf-8")
            parsed = json.loads(body or "{}")
            detail = str(parsed.get("message") or parsed.get("error") or "")
        except Exception:
            detail = ""
        raise RuntimeError(detail or f"License API returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"License API unavailable: {exc.reason}") from exc


def _remember_success(state: dict, response: dict) -> None:
    now = _utcnow()
    state["licenseKey"] = str(response.get("licenseKey") or state.get("licenseKey") or "").strip()
    state["activationId"] = response.get("activationId")
    state["lastValidatedAt"] = now.isoformat()
    state["offlineGraceUntil"] = (now + timedelta(hours=OFFLINE_GRACE_HOURS)).isoformat()
    state["shutdownRequested"] = bool(response.get("shutdownRequested"))
    state["shutdownReason"] = response.get("shutdownReason")
    save_state(state)


def _within_offline_grace(state: dict) -> bool:
    raw = str(state.get("offlineGraceUntil") or "").strip()
    if not raw:
        return False
    try:
        return datetime.fromisoformat(raw) >= _utcnow()
    except Exception:
        return False


def activate_license_key(license_key: str) -> LicenseCheckResult:
    if not license_api_enabled():
        raise RuntimeError("License activation is not enabled for this build.")
    state = load_state()
    payload = {
        "licenseKey": str(license_key or "").strip(),
        "machineFingerprint": state["machineFingerprint"],
        "deviceName": state["deviceName"],
        "appVersion": APP_VERSION,
        "sessionId": uuid.uuid4().hex,
    }
    response = _post_json("/api/v1/licenses/activate", payload)
    _remember_success(state, response)
    if bool(response.get("shutdownRequested")):
        return LicenseCheckResult(False, str(response.get("shutdownReason") or "This MediaSorter instance was shut down by the license service."), shutdown_requested=True)
    if not bool(response.get("validForCurrentMachine")):
        return LicenseCheckResult(False, "This license is not valid for this machine.")
    return LicenseCheckResult(True, "MediaSorter activated.")


def validate_license_state() -> LicenseCheckResult:
    if not license_api_enabled():
        return LicenseCheckResult(True, "License checks are disabled for this build.")
    state = load_state()
    license_key = str(state.get("licenseKey") or "").strip()
    if not license_key:
        return LicenseCheckResult(False, "Enter your MediaSorter license key to continue.")
    payload = {
        "licenseKey": license_key,
        "machineFingerprint": state["machineFingerprint"],
        "deviceName": state["deviceName"],
        "appVersion": APP_VERSION,
        "sessionId": uuid.uuid4().hex,
    }
    try:
        response = _post_json("/api/v1/licenses/validate", payload)
        _remember_success(state, response)
    except RuntimeError as exc:
        if _within_offline_grace(state):
            return LicenseCheckResult(True, "MediaSorter is using its cached offline grace period.", used_offline_grace=True)
        return LicenseCheckResult(False, str(exc))

    if bool(response.get("shutdownRequested")):
        return LicenseCheckResult(
            False,
            str(response.get("shutdownReason") or "This MediaSorter instance has been disabled."),
            shutdown_requested=True,
        )
    if not bool(response.get("validForCurrentMachine")):
        return LicenseCheckResult(False, "This license key is already assigned to another device.")
    return LicenseCheckResult(True, "License check passed.")


def ensure_gui_license(parent=None) -> bool:
    if not license_api_enabled():
        return True
    check = validate_license_state()
    if check.valid:
        return True
    if check.shutdown_requested:
        QMessageBox.critical(parent, "MediaSorter Disabled", check.message)
        return False

    while True:
        buy_hint = core.SUPPORT_URL or "your purchase page"
        prompt = (
            f"{check.message}\n\n"
            f"Enter your MediaSorter license key.\n"
            f"If you still need to buy one, use:\n{buy_hint}"
        )
        license_key, ok = QInputDialog.getText(parent, "Activate MediaSorter", prompt)
        if not ok:
            return False
        try:
            result = activate_license_key(license_key)
        except RuntimeError as exc:
            QMessageBox.warning(parent, "Activation Failed", str(exc))
            continue
        if result.shutdown_requested:
            QMessageBox.critical(parent, "MediaSorter Disabled", result.message)
            return False
        if result.valid:
            return True
        QMessageBox.warning(parent, "Activation Failed", result.message)


class LicenseMonitor:
    def __init__(self, app, window):
        self.app = app
        self.window = window
        self.timer = QTimer(window)
        self.timer.setInterval(LICENSE_HEARTBEAT_SECONDS * 1000)
        self.timer.timeout.connect(self._tick)
        self._lock = threading.Lock()

    def start(self) -> None:
        if not license_api_enabled():
            return
        self.timer.start()

    def _tick(self) -> None:
        if not self._lock.acquire(blocking=False):
            return

        def worker() -> None:
            try:
                result = validate_license_state()
            finally:
                self._lock.release()
            if result.valid:
                return
            if result.used_offline_grace:
                return

            def shutdown() -> None:
                QMessageBox.critical(self.window, "MediaSorter Disabled", result.message)
                try:
                    self.window.close()
                finally:
                    self.app.quit()

            QTimer.singleShot(0, shutdown)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
