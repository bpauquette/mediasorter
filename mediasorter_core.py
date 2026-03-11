import sys
import os
import atexit
from pathlib import Path
import shutil
import subprocess
import json
import hashlib
import sqlite3
import threading
import time
from datetime import datetime
import re
import traceback
import ctypes
if os.name == "nt":
    try:
        import winreg  # type: ignore
    except Exception:
        winreg = None  # type: ignore
else:
    winreg = None  # type: ignore
from PIL import Image, ImageFile
ImageFile.LOAD_TRUNCATED_IMAGES = True  # handle truncated JPEGs
import imageio.v3 as iio
import numpy as np
import warnings
import urllib.request
warnings.filterwarnings("ignore", message="QuickGELU mismatch")  # OpenCLIP warning
# Reduce noisy cache warnings on Windows (symlink limitations don't break functionality).
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

HAS_PILLOW_HEIF = False
_PILLOW_HEIF_IMPORT_ERROR = None
_PILLOW_HEIF_IMPORT_TRACE = None
_HEIC_LOAD_WARNING_SHOWN = False
_HEIC_BOOTSTRAP_DONE = False
_HEIC_DLL_DIR_HANDLES = []

# Try importing OpenCV if available
try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFileDialog, QComboBox, QProgressBar, QCheckBox, QMessageBox, QGroupBox,
    QDialog, QListWidget, QInputDialog, QLineEdit,
    QGraphicsView, QGraphicsScene, QGraphicsObject, QGraphicsRectItem, QGraphicsTextItem
)
from PySide6.QtGui import QPixmap, QDesktopServices, QPainter, QColor
from PySide6.QtCore import QUrl, Qt, QThread, Signal, QTimer, QPropertyAnimation, QEasingCurve, QPointF, QVariantAnimation

# ---------------------------
# CONFIGURATION
# ---------------------------

APP_NAME = "MediaSorter"
APP_AUTHOR = "MediaSorter"

def _get_data_dir() -> Path:
    override = os.environ.get("MEDIASORTER_DATA_DIR")
    if override:
        p = Path(override).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p
    try:
        from platformdirs import user_data_dir  # type: ignore
        p = Path(user_data_dir(APP_NAME, APP_AUTHOR))
    except Exception:
        # Fallback without platformdirs
        p = Path(os.environ.get("APPDATA") or (Path.home() / f".{APP_NAME.lower()}"))
    p.mkdir(parents=True, exist_ok=True)
    return p

DATA_DIR = _get_data_dir()
SCRIPT_DIR = Path(__file__).resolve().parent
LOG_FILE = DATA_DIR / "mediasorter.log"
DECISION_LOG_FILE = DATA_DIR / "classification_decisions.jsonl"
PRODUCT_EVENTS_FILE = DATA_DIR / "product_events.jsonl"
SEARCH_INDEX_FILE = DATA_DIR / "media_search.sqlite3"
_CLASSIFICATION_CONTEXT_BY_PATH = {}
_CLASSIFICATION_CONTEXT_MAX = 2000

def _log_line(msg: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass
    except Exception:
        pass


def _append_decision_log(record: dict) -> None:
    """Append a single structured classification/sort decision as JSONL."""
    try:
        if not isinstance(record, dict):
            return
        payload = dict(record)
        payload.setdefault("timestamp", datetime.now().isoformat(timespec="seconds"))
        DECISION_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(DECISION_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _pretty_category_name(cat: str) -> str:
    try:
        c = str(cat or "").strip()
        if not c:
            return "Uncategorized"
        return c.replace("_", " ")
    except Exception:
        return "Uncategorized"


def _coerce_topk_entries(topk_obj, max_items: int = 3) -> list[tuple[str, float]]:
    out = []
    try:
        for item in list(topk_obj or []):
            if isinstance(item, dict):
                cat = str(item.get("category") or "").strip()
                score = float(item.get("score") or 0.0)
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                cat = str(item[0] or "").strip()
                score = float(item[1] or 0.0)
            else:
                continue
            if not cat:
                continue
            out.append((cat, score))
            if len(out) >= int(max_items):
                break
    except Exception:
        return []
    return out


def _format_score(score) -> str:
    try:
        return f"{float(score):.2f}"
    except Exception:
        return "0.00"


def _category_prompt_examples(category: str) -> list[str]:
    c = str(category or "").strip().lower()
    if not c:
        return []
    if c == "family photo":
        return [
            "a family photo",
            "a photo of a person",
            "a close-up portrait photo of a person",
            "a portrait of a man",
            "a portrait of a woman",
            "a photo of people smiling",
            "a candid photo of a person indoors",
        ]
    if c == "selfie":
        return [
            "a selfie",
            "a selfie photo of a person",
            "a front camera selfie",
            "a selfie of a person indoors",
            "a mirror selfie",
        ]
    if c == "pet":
        return [
            "a photo of a pet",
            "a photo of a dog",
            "a photo of a cat",
            "a close-up photo of a dog",
            "a close-up photo of a cat",
            "a photo of an animal",
        ]
    if c == "shopping":
        return [
            "a photo of products on shelves in a store",
            "a photo of items on store shelves",
            "a photo of a grocery store aisle",
            "a photo of a retail store aisle",
            "a photo of a product display in a store",
        ]
    if c == "facebook download":
        return [
            "a photo downloaded from Facebook",
            "a photo saved from the Facebook app",
            "an image from a Facebook post",
            "a photo from a Facebook feed",
            "a re-shared image from Facebook",
        ]
    if c == "iphone screenshot":
        return [
            "an iPhone screenshot",
            "a screenshot of an iPhone home screen with app icons",
            "a screenshot of a mobile app interface on iOS",
            "a screenshot of a phone screen with iOS UI",
        ]
    if c == "political meme":
        return [
            "a political meme",
            "a meme about politics",
            "a political meme with text over an image",
            "a political infographic shared online",
        ]
    if c == "political cartoon":
        return [
            "a political cartoon",
            "an editorial cartoon about politics",
            "a satirical political cartoon",
            "a comic strip about politics",
        ]
    if c == "screenshot":
        return [
            "a screenshot",
            "a screenshot of a phone screen",
            "a screenshot of an app interface",
            "a screen capture of a website or app",
        ]
    if c == "document":
        return [
            "a photo of a document",
            "a photo of printed text on paper",
            "a scanned document",
            "a photo of a receipt",
            "a photo of a page of text",
        ]
    if c == "food":
        return [
            "a photo of food",
            "a photo of a meal on a plate",
            "a photo of a restaurant dish",
            "a close-up photo of food",
            "a photo of a drink",
        ]
    if c == "car":
        return [
            "a photo of a car",
            "a photo of a vehicle",
            "a photo of a car interior",
            "a photo of a parked car outdoors",
        ]
    if c == "landscape":
        return [
            "a landscape photo",
            "a photo of mountains",
            "a photo of the ocean",
            "a scenic outdoor landscape",
        ]
    if c == "outdoor structure":
        return [
            "a photo of an outdoor structure",
            "a photo of a wooden deck",
            "a photo of a porch or deck railing",
            "a photo of a fence or railing outdoors",
            "a photo of a patio, deck, or backyard structure",
        ]
    if c == "outdoor photo":
        return [
            "an outdoor photo",
            "a photo taken outside",
            "a photo of people outdoors",
            "an outdoor snapshot",
        ]
    if c == "indoor photo":
        return [
            "an indoor photo",
            "a photo taken inside",
            "a photo of people indoors",
            "an indoor snapshot",
        ]
    return [f"a photo of {c}", f"an image of {c}", f"a snapshot of {c}"]


def _category_prompt_hints(category: str, max_items: int = 3) -> list[str]:
    prompts = list(_category_prompt_examples(category) or [])

    out = []
    seen = set()
    prefixes = (
        "a close-up portrait photo of ",
        "a close-up photo of ",
        "a candid photo of ",
        "a portrait of ",
        "a screenshot of ",
        "a photo taken while ",
        "a photo taken ",
        "a photo of ",
        "an image of ",
        "a snapshot of ",
        "an outdoor ",
        "an indoor ",
    )

    for raw in prompts:
        txt = str(raw or "").strip()
        if not txt:
            continue
        low = txt.lower()
        for prefix in prefixes:
            if low.startswith(prefix):
                txt = txt[len(prefix):].strip()
                low = txt.lower()
                break
        if low.startswith("a "):
            txt = txt[2:].strip()
        elif low.startswith("an "):
            txt = txt[3:].strip()
        txt = txt.strip(" .")
        key = txt.casefold()
        if not txt or key in seen:
            continue
        seen.add(key)
        out.append(txt)
        if len(out) >= int(max_items):
            break
    return out


def _natural_language_join(parts: list[str]) -> str:
    items = [str(p or "").strip() for p in list(parts or []) if str(p or "").strip()]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _category_cue_sentence(category: str, max_items: int = 3, prefix: str = "The clearest visual cues were") -> str:
    cues = _category_prompt_hints(category, max_items=max_items)
    if not cues:
        return ""
    return f"{prefix} {_natural_language_join(cues)}."


def _category_hypothesis_blurb(final_cat: str, final_score: str, topk: list[tuple[str, float]] | None) -> str:
    alternatives = [(c, s) for (c, s) in list(topk or []) if str(c) != str(final_cat)][:2]

    parts = [f"The AI's strongest read was '{_pretty_category_name(final_cat)}' (score {final_score})."]
    if alternatives:
        parts.append(
            " Other plausible reads were "
            + ", ".join([f"'{_pretty_category_name(c)}' ({_format_score(s)})" for (c, s) in alternatives])
            + "."
        )
    cue_sentence = _category_cue_sentence(final_cat, max_items=3)
    if cue_sentence:
        parts.append(f" {cue_sentence}")
    return "".join(parts)


def _build_classification_explanation(decision: dict) -> str:
    reason = str((decision or {}).get("reason") or "")
    final_cat = _pretty_category_name((decision or {}).get("final_category"))
    final_score = _format_score((decision or {}).get("final_score"))
    model = (decision or {}).get("model") if isinstance((decision or {}).get("model"), dict) else {}
    topk = _coerce_topk_entries(model.get("topk"), max_items=3)
    model_readout = _category_hypothesis_blurb(final_cat, final_score, topk)
    final_cue_sentence = _category_cue_sentence(final_cat, max_items=3)

    if reason == "user_correction":
        return f"Used your previously confirmed label, so this image was placed in '{final_cat}'."
    if reason == "user_live_override":
        prev_cat = _pretty_category_name((decision or {}).get("previous_category"))
        return (
            f"You changed this file from '{prev_cat}' to '{final_cat}', so MediaSorter moved it to the updated destination."
        )
    if reason == "image_load_failed":
        return "The image could not be read reliably, so it was placed in 'Uncategorized'."
    if reason == "heuristics_only_provider":
        return (
            f"AI was disabled for this run, so filename/metadata heuristics were used and the image was placed in '{final_cat}'."
        )
    if reason == "heuristics_only_provider_no_match":
        return "AI was disabled and no strong heuristic match was found, so the image was placed in 'Uncategorized'."
    if reason == "model_not_ready":
        return "The AI model was not ready, so the image was placed in 'Uncategorized'."
    if reason == "face_override_pet_to_family_photo":
        frac = 0.0
        try:
            frac = float((decision or {}).get("face_fraction") or 0.0)
        except Exception:
            frac = 0.0
        pct = f"{frac * 100.0:.1f}%"
        return (
            "The model initially leaned toward 'pet', but a prominent detected face "
            f"({pct} of the frame) suggested a portrait, so it was placed in 'family photo'."
        )
    if reason == "heuristic_override_facebook_download":
        base = _pretty_category_name((decision or {}).get("heuristic_eligible_from"))
        cue_sentence = _category_cue_sentence(base or final_cat, max_items=3)
        body = (
            f"The AI result was in a screenshot/document-style bucket ('{base}'), and filename/metadata looked like a Facebook export, "
            "so it was placed in 'facebook download'."
        )
        return f"{cue_sentence} {body}".strip() if cue_sentence else body
    if reason == "heuristic_override_screenshot_family":
        base = _pretty_category_name((decision or {}).get("heuristic_eligible_from"))
        cue_sentence = _category_cue_sentence(final_cat or base, max_items=3)
        body = (
            f"The AI result was in the screenshot/document family ('{base}'), and screen-capture heuristics matched, "
            f"so it was placed in '{final_cat}'."
        )
        return f"{cue_sentence} {body}".strip() if cue_sentence else body

    noise = model.get("noise_adjustment") if isinstance(model, dict) else {}
    if isinstance(noise, dict) and bool(noise.get("applied")):
        from_cat = _pretty_category_name(noise.get("from_category"))
        to_cat = _pretty_category_name(noise.get("to_category"))
        return (
            f"The image's visual patterns were a near tie, so a stability rule switched the category from '{from_cat}' to '{to_cat}'."
            f" {model_readout}"
        )

    if topk:
        return model_readout

    if final_cue_sentence:
        return f"The AI placed this image in '{final_cat}' with score {final_score}. {final_cue_sentence}"
    return f"The AI placed this image in '{final_cat}' with score {final_score}."


def _classification_explanation_source(decision: dict) -> str:
    reason = str((decision or {}).get("reason") or "").strip().lower()
    model = (decision or {}).get("model") if isinstance((decision or {}).get("model"), dict) else {}
    topk = _coerce_topk_entries(model.get("topk"), max_items=3)

    if reason in ("user_correction", "user_live_override"):
        return "user_override"
    if reason in ("image_load_failed", "heuristics_only_provider_no_match", "model_not_ready"):
        return "system_fallback"
    if reason.startswith("heuristic_override_") or reason in (
        "heuristics_only_provider",
        "face_override_pet_to_family_photo",
    ):
        return "rule_based_override"
    if reason == "model_prediction" and topk:
        return "category_template"
    if topk:
        return "category_template"
    return "unknown"


def _cache_classification_context(decision: dict) -> None:
    try:
        path = str((decision or {}).get("file_path") or "").strip()
        if not path:
            return
        _CLASSIFICATION_CONTEXT_BY_PATH[path] = {
            "final_category": str((decision or {}).get("final_category") or "Uncategorized"),
            "final_score": float((decision or {}).get("final_score") or 0.0),
            "reason": str((decision or {}).get("reason") or ""),
            "explanation": str((decision or {}).get("explanation") or ""),
            "explanation_source": str((decision or {}).get("explanation_source") or ""),
        }
        while len(_CLASSIFICATION_CONTEXT_BY_PATH) > int(_CLASSIFICATION_CONTEXT_MAX):
            try:
                _CLASSIFICATION_CONTEXT_BY_PATH.pop(next(iter(_CLASSIFICATION_CONTEXT_BY_PATH)))
            except Exception:
                break
    except Exception:
        pass


def _finalize_classification_decision(decision: dict) -> None:
    try:
        if not isinstance(decision, dict):
            return
        decision["explanation"] = _build_classification_explanation(decision)
        decision["explanation_source"] = _classification_explanation_source(decision)
        _cache_classification_context(decision)
        _append_decision_log(decision)
    except Exception:
        _append_decision_log(decision)


def get_decision_log_path() -> str:
    return str(DECISION_LOG_FILE)


def get_search_index_path() -> str:
    return str(SEARCH_INDEX_FILE)


def _connect_search_index() -> sqlite3.Connection:
    SEARCH_INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(SEARCH_INDEX_FILE))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    return conn


def _ensure_search_index_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS media_search_index (
            result_path TEXT PRIMARY KEY,
            source_path TEXT NOT NULL DEFAULT '',
            file_name TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT '',
            explanation TEXT NOT NULL DEFAULT '',
            search_text TEXT NOT NULL DEFAULT '',
            flow TEXT NOT NULL DEFAULT '',
            file_kind TEXT NOT NULL DEFAULT '',
            tokens_json TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_media_search_updated_at ON media_search_index(updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_media_search_category ON media_search_index(category)"
    )
    conn.commit()


def _search_terms_for_category(category: str) -> list[str]:
    cat = str(category or "").strip()
    if not cat:
        return []
    prompts = [str(p).strip() for p in (_category_prompt_examples(cat) or []) if str(p).strip()]
    return prompts if prompts else [cat, f"a photo of {cat}", f"an image of {cat}"]


def _build_search_document(record: dict) -> tuple[str, str, str]:
    result_path = str((record or {}).get("dest_path") or (record or {}).get("source_path") or "").strip()
    source_path = str((record or {}).get("source_path") or "").strip()
    file_name = str((record or {}).get("file_name") or os.path.basename(result_path or source_path or "")).strip()
    category = str(
        (record or {}).get("classification_final_category")
        or (record or {}).get("category")
        or "Uncategorized"
    ).strip() or "Uncategorized"
    explanation = str((record or {}).get("classification_explanation") or "").strip()
    reason = str((record or {}).get("classification_reason") or "").strip()
    flow = str((record or {}).get("flow") or "").strip()
    tokens = (record or {}).get("tokens") if isinstance((record or {}).get("tokens"), dict) else {}

    token_values = []
    for key, value in dict(tokens or {}).items():
        key_txt = str(key or "").strip()
        value_txt = str(value or "").strip()
        if not key_txt and not value_txt:
            continue
        token_values.append(f"{key_txt} {value_txt}".strip())

    file_kind = "video" if str(category).strip().lower() == "videos" else "image"
    kind_terms = "video movie clip" if file_kind == "video" else "image photo picture"
    prompt_terms = " ".join(_search_terms_for_category(category))

    parts = [
        file_name,
        category,
        explanation,
        reason,
        flow,
        source_path,
        result_path,
        kind_terms,
        prompt_terms,
        " ".join(token_values),
    ]
    raw_text = " ".join([p for p in parts if p]).strip()
    search_text = re.sub(r"\s+", " ", raw_text).strip().lower()
    return category, explanation, search_text


def _upsert_search_index_record(conn: sqlite3.Connection, record: dict) -> bool:
    result_path = str((record or {}).get("dest_path") or (record or {}).get("source_path") or "").strip()
    if not result_path:
        return False

    source_path = str((record or {}).get("source_path") or "").strip()
    file_name = str((record or {}).get("file_name") or os.path.basename(result_path)).strip()
    flow = str((record or {}).get("flow") or "").strip()
    tokens = (record or {}).get("tokens") if isinstance((record or {}).get("tokens"), dict) else {}
    file_kind = "video" if str((record or {}).get("category") or "").strip().lower() == "videos" else "image"
    category, explanation, search_text = _build_search_document(record)

    conn.execute(
        """
        INSERT OR REPLACE INTO media_search_index (
            result_path,
            source_path,
            file_name,
            category,
            explanation,
            search_text,
            flow,
            file_kind,
            tokens_json,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            result_path,
            source_path,
            file_name,
            category,
            explanation,
            search_text,
            flow,
            file_kind,
            json.dumps(tokens, ensure_ascii=False, sort_keys=True),
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    return True


def index_search_record(record: dict) -> bool:
    try:
        with _connect_search_index() as conn:
            _ensure_search_index_schema(conn)
            changed = _upsert_search_index_record(conn, record)
            conn.commit()
            return bool(changed)
    except Exception:
        return False


def rebuild_search_index_from_decision_log() -> dict:
    stats = {
        "indexed": 0,
        "seen_sort_records": 0,
        "db_path": str(SEARCH_INDEX_FILE),
        "log_path": str(DECISION_LOG_FILE),
    }
    try:
        with _connect_search_index() as conn:
            _ensure_search_index_schema(conn)
            conn.execute("DELETE FROM media_search_index")

            if DECISION_LOG_FILE.exists():
                with open(DECISION_LOG_FILE, "r", encoding="utf-8") as f:
                    for line in f:
                        line = str(line or "").strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                        except Exception:
                            continue
                        if str((record or {}).get("event") or "") != "sort_destination":
                            continue
                        stats["seen_sort_records"] += 1
                        if _upsert_search_index_record(conn, record):
                            stats["indexed"] += 1
            conn.commit()
    except Exception:
        pass
    return stats


def search_media_index(query: str, limit: int = 50) -> list[dict]:
    out = []
    q = str(query or "").strip().lower()
    tokens = [t for t in re.split(r"\s+", q) if t]
    try:
        needs_rebuild = not SEARCH_INDEX_FILE.exists()
        if not needs_rebuild:
            try:
                with _connect_search_index() as probe:
                    _ensure_search_index_schema(probe)
                    row = probe.execute("SELECT COUNT(*) AS c FROM media_search_index").fetchone()
                    needs_rebuild = int((row["c"] if row is not None else 0) or 0) <= 0
            except Exception:
                needs_rebuild = True

        if needs_rebuild:
            rebuild_search_index_from_decision_log()

        with _connect_search_index() as conn:
            _ensure_search_index_schema(conn)
            sql = (
                "SELECT result_path, source_path, file_name, category, explanation, flow, file_kind, updated_at "
                "FROM media_search_index"
            )
            params = []
            if tokens:
                clauses = []
                for token in tokens:
                    clauses.append("search_text LIKE ?")
                    params.append(f"%{token}%")
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY updated_at DESC, file_name COLLATE NOCASE ASC LIMIT ?"
            params.append(max(1, min(int(limit or 50), 200)))

            rows = conn.execute(sql, params).fetchall()
            for row in rows:
                out.append(
                    {
                        "path": str(row["result_path"] or ""),
                        "source_path": str(row["source_path"] or ""),
                        "file_name": str(row["file_name"] or ""),
                        "category": str(row["category"] or ""),
                        "explanation": str(row["explanation"] or ""),
                        "flow": str(row["flow"] or ""),
                        "file_kind": str(row["file_kind"] or ""),
                        "updated_at": str(row["updated_at"] or ""),
                    }
                )
    except Exception:
        return []
    return out


def log_product_event(event: str, data: dict | None = None) -> None:
    """Append lightweight product/UX telemetry events to local JSONL."""
    try:
        if not event:
            return
        payload = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "event": str(event),
            "data": dict(data or {}),
        }
        PRODUCT_EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(PRODUCT_EVENTS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _log_sort_destination_decision(
    source_path: str,
    category: str,
    structure_pattern: str,
    tokens: dict,
    dest_dir: str,
    dest_path: str,
    flow: str = "auto",
) -> None:
    payload = {
        "event": "sort_destination",
        "flow": str(flow or "auto"),
        "source_path": str(source_path or ""),
        "file_name": os.path.basename(str(source_path or "")),
        "category": str(category or "Uncategorized"),
        "structure_pattern": str(structure_pattern or "{category}"),
        "tokens": dict(tokens or {}),
        "dest_dir": str(dest_dir or ""),
        "dest_path": str(dest_path or ""),
    }
    try:
        ctx = _CLASSIFICATION_CONTEXT_BY_PATH.get(str(source_path or ""))
        if isinstance(ctx, dict):
            payload["classification_reason"] = str(ctx.get("reason") or "")
            payload["classification_explanation"] = str(ctx.get("explanation") or "")
            payload["classification_explanation_source"] = str(ctx.get("explanation_source") or "")
            payload["classification_final_category"] = str(ctx.get("final_category") or payload["category"])
            payload["classification_final_score"] = float(ctx.get("final_score") or 0.0)
    except Exception:
        pass
    if "classification_explanation" not in payload:
        try:
            if str(category or "").strip().lower() == "videos":
                payload["classification_explanation"] = "This file was detected as a video and placed in the Videos destination."
            else:
                payload["classification_explanation"] = (
                    f"The image was sorted into '{_pretty_category_name(category)}' based on the current classification result."
                )
        except Exception:
            pass
    if "classification_explanation_source" not in payload:
        try:
            if str(category or "").strip().lower() == "videos":
                payload["classification_explanation_source"] = "rule_based_video"
            else:
                payload["classification_explanation_source"] = "unknown"
        except Exception:
            pass
    _append_decision_log(payload)
    try:
        index_search_record(payload)
    except Exception:
        pass


def apply_live_category_override(
    source_path: str,
    current_dest_path: str,
    new_category: str,
    output_folder: str,
    structure_pattern: str = "{category}",
    previous_category: str = "",
) -> dict:
    src = str(source_path or "").strip()
    current_dest = str(current_dest_path or "").strip()
    out_root = str(output_folder or "").strip()
    new_cat = str(new_category or "").strip()
    prev_cat = str(previous_category or "").strip() or "Uncategorized"

    if not src:
        raise ValueError("Missing source file path.")
    if not out_root:
        raise ValueError("Missing output folder.")
    if not new_cat:
        raise ValueError("Choose a category first.")
    if new_cat not in CATEGORIES:
        raise ValueError(f"Unknown category: {new_cat}")
    if str(src).lower().endswith(VIDEO_EXT):
        raise ValueError("Live category override is only supported for image files.")

    img = load_image_for_ai(src)
    toks = _structure_tokens(new_cat, src, img)
    dest_dir = _render_structure(out_root, structure_pattern or "{category}", toks)
    file_name = os.path.basename(current_dest or src)
    preferred_dest = os.path.join(dest_dir, file_name)

    same_dest = False
    try:
        same_dest = os.path.normcase(os.path.abspath(preferred_dest)) == os.path.normcase(os.path.abspath(current_dest))
    except Exception:
        same_dest = preferred_dest == current_dest

    if same_dest and current_dest:
        final_dest = current_dest
    else:
        final_dest = _unique_dest_path(dest_dir, file_name)
        if current_dest and os.path.exists(current_dest):
            os.makedirs(os.path.dirname(final_dest), exist_ok=True)
            shutil.move(current_dest, final_dest)
        else:
            os.makedirs(os.path.dirname(final_dest), exist_ok=True)
            shutil.copy2(src, final_dest)

    img_hash = hash_image(src)
    if img_hash:
        CORRECTIONS[img_hash] = new_cat
        try:
            _atomic_write_json(CORRECTION_FILE, CORRECTIONS)
        except Exception:
            pass

    try:
        if img is not None:
            _old_cat, _old_score, emb = _predict_category_from_pil(img)
            _update_prototype(new_cat, emb)
    except Exception:
        pass

    _CLASSIFICATION_CONTEXT_BY_PATH[src] = {
        "final_category": new_cat,
        "final_score": 1.0,
        "reason": "user_live_override",
        "explanation": (
            f"You changed this file from '{_pretty_category_name(prev_cat)}' to "
            f"'{_pretty_category_name(new_cat)}', so MediaSorter moved it to the updated destination."
        ),
        "previous_category": prev_cat,
    }

    _log_sort_destination_decision(
        source_path=src,
        category=new_cat,
        structure_pattern=structure_pattern or "{category}",
        tokens=toks,
        dest_dir=dest_dir,
        dest_path=final_dest,
        flow="live_override",
    )

    return {
        "source_path": src,
        "dest_path": final_dest,
        "category": new_cat,
        "previous_category": prev_cat,
        "explanation": str(_CLASSIFICATION_CONTEXT_BY_PATH[src].get("explanation") or ""),
    }


def _runtime_binary_dirs() -> list:
    dirs = []
    try:
        dirs.append(Path(sys.executable).resolve().parent)
    except Exception:
        pass
    try:
        dirs.append(Path(__file__).resolve().parent)
    except Exception:
        pass

    seen = set()
    out = []
    for d in dirs:
        try:
            key = str(d).lower()
            if key in seen:
                continue
            seen.add(key)
            if d.exists():
                out.append(d)
        except Exception:
            continue
    return out


def _register_windows_dll_dirs(dirs: list) -> None:
    global _HEIC_DLL_DIR_HANDLES
    if os.name != "nt" or not hasattr(os, "add_dll_directory"):
        return
    for d in dirs:
        try:
            handle = os.add_dll_directory(str(d))
            _HEIC_DLL_DIR_HANDLES.append(handle)
        except Exception:
            pass


def _prime_heif_native_libs(dirs: list) -> None:
    if os.name != "nt":
        return
    patterns = (
        "libheif-*.dll",
        "libde265-*.dll",
        "libx265-*.dll",
        "libstdc++-*.dll",
        "libgcc_s_seh-*.dll",
        "libwinpthread-*.dll",
    )
    loaded = set()
    for d in dirs:
        for pat in patterns:
            try:
                for dll in d.glob(pat):
                    p = str(dll.resolve())
                    if p in loaded:
                        continue
                    ctypes.WinDLL(p)
                    loaded.add(p)
            except Exception:
                continue


def _init_heic_support(force: bool = False) -> None:
    global HAS_PILLOW_HEIF, _PILLOW_HEIF_IMPORT_ERROR, _PILLOW_HEIF_IMPORT_TRACE, _HEIC_BOOTSTRAP_DONE
    if _HEIC_BOOTSTRAP_DONE and not force:
        return
    _HEIC_BOOTSTRAP_DONE = True
    HAS_PILLOW_HEIF = False
    _PILLOW_HEIF_IMPORT_ERROR = None
    _PILLOW_HEIF_IMPORT_TRACE = None
    try:
        bin_dirs = _runtime_binary_dirs()
        _register_windows_dll_dirs(bin_dirs)
        _prime_heif_native_libs(bin_dirs)
        import pillow_heif  # type: ignore

        pillow_heif.register_heif_opener()
        HAS_PILLOW_HEIF = True
    except Exception as e:
        _PILLOW_HEIF_IMPORT_ERROR = f"{type(e).__name__}: {e}"
        _PILLOW_HEIF_IMPORT_TRACE = traceback.format_exc(limit=8)
        _log_line(f"[heic][init-error] {_PILLOW_HEIF_IMPORT_ERROR}")
        try:
            for line in (_PILLOW_HEIF_IMPORT_TRACE or "").splitlines():
                if line.strip():
                    _log_line(f"[heic][trace] {line.rstrip()}")
        except Exception:
            pass


_init_heic_support()

def _fmt_bytes(num_bytes: int) -> str:
    try:
        n = float(num_bytes)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024.0 or unit == "TB":
                if unit == "B":
                    return f"{int(n)} {unit}"
                return f"{n:.2f} {unit}"
            n /= 1024.0
    except Exception:
        return str(num_bytes)

def _fmt_duration(seconds: float) -> str:
    try:
        s = int(max(0.0, float(seconds)))
    except Exception:
        return "?"
    h = s // 3600
    m = (s % 3600) // 60
    ss = s % 60
    if h > 0:
        return f"{h:d}:{m:02d}:{ss:02d}"
    return f"{m:d}:{ss:02d}"

def _safe_folder_name(s: str) -> str:
    # Windows-safe (minimal) folder name sanitizer.
    s = (s or "").strip()
    if not s:
        return "Unknown"
    bad = '<>:"/\\\\|?*'
    out = "".join(("_" if c in bad else c) for c in s)
    out = out.strip().strip(".")
    return out or "Unknown"

def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)

def _atomic_write_json(path: Path, obj) -> None:
    _atomic_write_text(path, json.dumps(obj, indent=2, ensure_ascii=False))

HANDBRAKE_PATH = r"C:\Program Files\HandBrake\HandBrakeCLI.exe"
CATEGORIES_FILE = DATA_DIR / "categories.txt"
CORRECTION_FILE = DATA_DIR / "user_corrections.json"
PROTOTYPES_FILE = DATA_DIR / "category_prototypes.json"
PEOPLE_FILE = DATA_DIR / "people_db.json"

IMAGE_EXT = ('.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tiff', '.heic', '.heif')
VIDEO_EXT = ('.mov', '.m4v', '.mp4', '.hevc')

# Quick monetization support:
# point this at a hosted checkout page (Gumroad, Stripe Payment Link, etc.).
def _read_support_url_from_runtime_files() -> str:
    candidates = []
    try:
        candidates.append(Path(sys.executable).resolve().parent / "support_url.txt")
    except Exception:
        pass
    try:
        candidates.append(Path(__file__).resolve().parent / "support_url.txt")
    except Exception:
        pass

    seen = set()
    for p in candidates:
        try:
            key = str(p.resolve()).lower()
            if key in seen or not p.exists():
                continue
            seen.add(key)
            line = (p.read_text(encoding="utf-8", errors="ignore") or "").strip()
            if line:
                return line.splitlines()[0].strip()
        except Exception:
            continue
    return ""


DEFAULT_SUPPORT_URL = "https://github.com/bpauquette/mediasorter/releases/latest"
SUPPORT_URL = (
    os.environ.get("MEDIASORTER_SUPPORT_URL")
    or os.environ.get("MEDIASORTER_PAYMENT_URL")
    or _read_support_url_from_runtime_files()
    or DEFAULT_SUPPORT_URL
).strip()

REPO_MAIN_URL = "https://github.com/bpauquette/mediasorter/blob/main"
LEGAL_INFO_URL = (
    os.environ.get("MEDIASORTER_LEGAL_URL")
    or f"{REPO_MAIN_URL}/LEGAL_MARKETING_RECOMMENDATIONS.md"
).strip()
PRIVACY_URL = (
    os.environ.get("MEDIASORTER_PRIVACY_URL")
    or f"{REPO_MAIN_URL}/PRIVACY.md"
).strip()
TERMS_URL = (
    os.environ.get("MEDIASORTER_TERMS_URL")
    or f"{REPO_MAIN_URL}/TERMS.md"
).strip()
REFUND_URL = (
    os.environ.get("MEDIASORTER_REFUND_URL")
    or f"{REPO_MAIN_URL}/REFUND_POLICY.md"
).strip()

SEQUOIAVIEW_URL = (
    os.environ.get("MEDIASORTER_SEQUOIAVIEW_URL")
    or "https://www.win.tue.nl/sequoiaview/"
).strip()


def _clean_exe_candidate_path(raw_path: str) -> str:
    p = str(raw_path or "").strip().strip('"')
    if not p:
        return ""
    low = p.lower()
    if ".exe" in low:
        p = p[: low.find(".exe") + 4]
    return p.strip().strip('"')


def _append_sequoiaview_candidate(candidates: list[str], seen: set[str], raw_path) -> None:
    try:
        p = _clean_exe_candidate_path(str(raw_path or ""))
        if not p:
            return
        p = os.path.expandvars(os.path.expanduser(p))
        key = p.lower()
        if key in seen:
            return
        seen.add(key)
        candidates.append(p)
    except Exception:
        return


def _sequoiaview_paths_from_registry() -> list[str]:
    out = []
    if os.name != "nt" or winreg is None:
        return out

    seen = set()
    uninstall_roots = [
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    ]
    app_path_roots = [
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\SequoiaView.exe",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\SequoiaView.exe",
    ]
    roots = [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]

    for root in roots:
        for base in uninstall_roots:
            try:
                with winreg.OpenKey(root, base) as uninstall_key:
                    subkey_count, _, _ = winreg.QueryInfoKey(uninstall_key)
                    for i in range(subkey_count):
                        try:
                            subkey_name = winreg.EnumKey(uninstall_key, i)
                            with winreg.OpenKey(uninstall_key, subkey_name) as app_key:
                                try:
                                    display_name = str(winreg.QueryValueEx(app_key, "DisplayName")[0] or "")
                                except Exception:
                                    display_name = ""
                                low_name = display_name.lower()
                                if "sequoia" not in low_name or "view" not in low_name:
                                    continue

                                try:
                                    install_location = str(winreg.QueryValueEx(app_key, "InstallLocation")[0] or "")
                                except Exception:
                                    install_location = ""
                                if install_location:
                                    if ".exe" in install_location.lower():
                                        _append_sequoiaview_candidate(out, seen, install_location)
                                    else:
                                        _append_sequoiaview_candidate(
                                            out,
                                            seen,
                                            os.path.join(install_location, "SequoiaView.exe"),
                                        )

                                try:
                                    display_icon = str(winreg.QueryValueEx(app_key, "DisplayIcon")[0] or "")
                                except Exception:
                                    display_icon = ""
                                if display_icon:
                                    _append_sequoiaview_candidate(out, seen, display_icon)
                        except Exception:
                            continue
            except Exception:
                continue

        for app_path_key in app_path_roots:
            try:
                with winreg.OpenKey(root, app_path_key) as app_key:
                    try:
                        exe_path = str(winreg.QueryValueEx(app_key, "")[0] or "")
                    except Exception:
                        exe_path = ""
                    if exe_path:
                        _append_sequoiaview_candidate(out, seen, exe_path)
                    try:
                        base_path = str(winreg.QueryValueEx(app_key, "Path")[0] or "")
                    except Exception:
                        base_path = ""
                    if base_path:
                        _append_sequoiaview_candidate(out, seen, os.path.join(base_path, "SequoiaView.exe"))
            except Exception:
                continue

    return out


def get_sequoiaview_search_paths() -> list[str]:
    candidates = []
    seen = set()

    _append_sequoiaview_candidate(candidates, seen, shutil.which("SequoiaView.exe"))
    _append_sequoiaview_candidate(candidates, seen, shutil.which("SequoiaView"))
    _append_sequoiaview_candidate(candidates, seen, shutil.which("Sequoia.exe"))
    _append_sequoiaview_candidate(candidates, seen, shutil.which("Sequoia"))

    program_files = os.environ.get("ProgramFiles") or r"C:\Program Files"
    program_files_x86 = os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"
    local_app_data = os.environ.get("LocalAppData") or ""

    _append_sequoiaview_candidate(candidates, seen, os.path.join(program_files, "SequoiaView", "SequoiaView.exe"))
    _append_sequoiaview_candidate(candidates, seen, os.path.join(program_files_x86, "SequoiaView", "SequoiaView.exe"))
    _append_sequoiaview_candidate(candidates, seen, os.path.join(program_files, "SequoiaView", "Sequoia.exe"))
    _append_sequoiaview_candidate(candidates, seen, os.path.join(program_files_x86, "SequoiaView", "Sequoia.exe"))
    if local_app_data:
        _append_sequoiaview_candidate(
            candidates,
            seen,
            os.path.join(local_app_data, "Programs", "SequoiaView", "SequoiaView.exe"),
        )
        _append_sequoiaview_candidate(
            candidates,
            seen,
            os.path.join(local_app_data, "Programs", "SequoiaView", "Sequoia.exe"),
        )
        _append_sequoiaview_candidate(candidates, seen, os.path.join(local_app_data, "SequoiaView", "SequoiaView.exe"))
        _append_sequoiaview_candidate(candidates, seen, os.path.join(local_app_data, "SequoiaView", "Sequoia.exe"))

    for reg_path in _sequoiaview_paths_from_registry():
        _append_sequoiaview_candidate(candidates, seen, reg_path)

    return candidates


def find_sequoiaview_executable() -> str:
    for p in get_sequoiaview_search_paths():
        try:
            if Path(p).is_file():
                return str(Path(p).resolve())
        except Exception:
            continue
    return ""


def launch_sequoiaview(target_path: str | None = None) -> tuple[bool, str]:
    exe_path = find_sequoiaview_executable()
    if not exe_path:
        return False, "SequoiaView was not found in common install locations."
    try:
        args = [exe_path]
        target = str(target_path or "").strip()
        if target:
            args.append(target)
        if os.name == "nt":
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True, exe_path
    except Exception as e:
        return False, f"Found SequoiaView at '{exe_path}', but launch failed: {e}"


def get_heic_support_status() -> dict:
    """Return HEIC/HEIF decoder availability details for UI/CLI diagnostics."""
    try:
        _init_heic_support()
        ext_map = Image.registered_extensions()
        heic_fmt = ext_map.get(".heic")
        heif_fmt = ext_map.get(".heif")
        heic_decoder = bool(heic_fmt and heic_fmt in Image.OPEN)
        heif_decoder = bool(heif_fmt and heif_fmt in Image.OPEN)
        supported = bool(heic_decoder or heif_decoder)
        backend = "pillow-heif" if supported and HAS_PILLOW_HEIF else "unknown"
        detail = (
            "HEIC/HEIF decoding available via Pillow."
            if supported
            else (
                "HEIC/HEIF decoding not available. Install pillow-heif in this runtime."
                if not HAS_PILLOW_HEIF
                else "HEIC/HEIF plugin loaded, but decoder registration is missing."
            )
        )
        if _PILLOW_HEIF_IMPORT_ERROR and not supported:
            detail = f"{detail} ({_PILLOW_HEIF_IMPORT_ERROR})"
            low = (_PILLOW_HEIF_IMPORT_ERROR or "").lower()
            if "dll load failed" in low or "winerror 126" in low or "winerror 193" in low:
                detail = (
                    f"{detail} Native HEIF library loading failed in this runtime. "
                    "Ensure the bundled EXE includes pillow-heif native DLLs and matching architecture."
                )
        return {
            "supported": supported,
            "backend": backend,
            "heic_decoder": heic_decoder,
            "heif_decoder": heif_decoder,
            "detail": detail,
            "import_error": _PILLOW_HEIF_IMPORT_ERROR,
        }
    except Exception as e:
        return {
            "supported": False,
            "backend": "unknown",
            "heic_decoder": False,
            "heif_decoder": False,
            "detail": f"HEIC capability check failed: {e}",
            "import_error": _PILLOW_HEIF_IMPORT_ERROR,
        }


def get_face_support_status() -> dict:
    """Return face-identification availability details for UI diagnostics."""
    try:
        if not HAS_CV2:
            return {
                "supported": False,
                "has_cv2": False,
                "has_face_detector": False,
                "has_face_recognizer": False,
                "detail": (
                    "Face identification is unavailable because OpenCV is missing in this runtime. "
                    "Install dependencies from requirements.txt (opencv-contrib-python-headless)."
                ),
            }

        has_face_detector = bool(hasattr(cv2, "FaceDetectorYN"))
        has_face_recognizer = bool(hasattr(cv2, "FaceRecognizerSF"))
        supported = bool(has_face_detector and has_face_recognizer)
        detail = (
            "Face identification is available."
            if supported
            else (
                "This OpenCV build is missing required face modules. "
                "Use opencv-contrib-python-headless in this runtime."
            )
        )
        return {
            "supported": supported,
            "has_cv2": True,
            "has_face_detector": has_face_detector,
            "has_face_recognizer": has_face_recognizer,
            "detail": detail,
        }
    except Exception as e:
        return {
            "supported": False,
            "has_cv2": bool(HAS_CV2),
            "has_face_detector": False,
            "has_face_recognizer": False,
            "detail": f"Face capability check failed: {e}",
        }

# ---------------------------
# LOAD CATEGORIES
# ---------------------------

def _migrate_legacy_file(legacy_name: str, dest: Path) -> None:
    # Older versions stored state next to the script. If present, copy once into DATA_DIR.
    try:
        legacy_path = SCRIPT_DIR / legacy_name
        if legacy_path.exists() and not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(legacy_path, dest)
    except Exception:
        pass

_migrate_legacy_file("categories.txt", CATEGORIES_FILE)
_migrate_legacy_file("user_corrections.json", CORRECTION_FILE)
_migrate_legacy_file("category_prototypes.json", PROTOTYPES_FILE)

DEFAULT_CATEGORIES = [
    "family photo",
    "selfie",
    "pet",
    "landscape",
    "food",
    "shopping",
    "facebook download",
    "political meme",
    "political cartoon",
    "document",
    "screenshot",
    "iphone screenshot",
    "car",
    "outdoor structure",
    "indoor photo",
    "outdoor photo",
]

# Canonicalize common category variants so learned data does not fragment.
_CATEGORY_ALIASES = {
    "family": "family photo",
    "family photos": "family photo",
    "selfies": "selfie",
    "pets": "pet",
    "cars": "car",
    "documents": "document",
    "doc": "document",
    "screenshots": "screenshot",
    "screen shot": "screenshot",
    "iphone screenshots": "iphone screenshot",
    "iphone screen shot": "iphone screenshot",
    "facebook": "facebook download",
    "fb download": "facebook download",
    "fb image": "facebook download",
    "outdoor": "outdoor photo",
    "outdoors": "outdoor photo",
    "deck": "outdoor structure",
    "porch": "outdoor structure",
    "patio": "outdoor structure",
    "fence": "outdoor structure",
    "railing": "outdoor structure",
    "indoor": "indoor photo",
    "indoors": "indoor photo",
}

_REQUIRED_CATEGORIES = (
    "shopping",
    "facebook download",
    "iphone screenshot",
    "political meme",
    "political cartoon",
    "outdoor structure",
)

_GENERIC_CATEGORIES = {"family photo", "selfie", "indoor photo", "outdoor photo"}
_GENERIC_SWITCH_MARGIN = 0.015
_FAMILY_STABILITY_CATEGORIES = {"selfie", "indoor photo"}
_FAMILY_SWITCH_MARGIN = 0.010


def _canonical_category_name(name: str) -> str:
    s = (name or "").strip().lower()
    if not s:
        return ""
    s = re.sub(r"\s+", " ", s)
    return _CATEGORY_ALIASES.get(s, s)


def _normalize_category_list(values) -> list[str]:
    out = []
    seen = set()
    for raw in values or []:
        cat = _canonical_category_name(raw)
        if not cat or cat in seen:
            continue
        seen.add(cat)
        out.append(cat)
    return out

if not CATEGORIES_FILE.exists():
    _atomic_write_text(CATEGORIES_FILE, "\n".join(DEFAULT_CATEGORIES) + "\n")

try:
    CATEGORIES = [line.strip() for line in CATEGORIES_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
except Exception:
    CATEGORIES = DEFAULT_CATEGORIES[:]

# Normalize aliases and ensure required categories exist for existing installs too.
try:
    original = CATEGORIES[:]
    CATEGORIES = _normalize_category_list(CATEGORIES) or DEFAULT_CATEGORIES[:]
    changed = False
    for _c in _REQUIRED_CATEGORIES:
        if _c not in CATEGORIES:
            CATEGORIES.append(_c)
            changed = True
    if changed or CATEGORIES != original:
        _atomic_write_text(CATEGORIES_FILE, "\n".join(CATEGORIES) + "\n")
except Exception:
    pass

try:
    if CORRECTION_FILE.exists():
        CORRECTIONS = json.loads(CORRECTION_FILE.read_text(encoding="utf-8"))
    else:
        CORRECTIONS = {}
except Exception:
    CORRECTIONS = {}


def _normalize_corrections(raw: dict) -> tuple[dict, bool]:
    if not isinstance(raw, dict):
        return {}, True
    out = {}
    changed = False
    valid = set(CATEGORIES)
    for h, cat in raw.items():
        if not isinstance(h, str):
            changed = True
            continue
        if not isinstance(cat, str):
            changed = True
            continue
        c = _canonical_category_name(cat)
        if not c or c not in valid:
            changed = True
            continue
        prev = out.get(h)
        if prev is not None and prev != c:
            changed = True
        out[h] = c
        if c != cat:
            changed = True
    return out, changed


try:
    CORRECTIONS, _corr_changed = _normalize_corrections(CORRECTIONS)
    if _corr_changed:
        _atomic_write_json(CORRECTION_FILE, CORRECTIONS)
except Exception:
    pass

def _load_prototypes() -> dict:
    if not PROTOTYPES_FILE.exists():
        return {}
    try:
        raw = json.loads(PROTOTYPES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

    # v1 format
    if isinstance(raw, dict) and "prototypes" in raw and isinstance(raw["prototypes"], dict):
        protos = raw["prototypes"]
    else:
        # legacy format: {category: {...}} or {category: [floats]}
        protos = raw if isinstance(raw, dict) else {}

    out = {}
    valid = set(CATEGORIES)
    for k, v in protos.items():
        if not isinstance(k, str) or not k.strip():
            continue
        cat = _canonical_category_name(k)
        if not cat or cat not in valid:
            continue
        if isinstance(v, dict) and isinstance(v.get("embedding"), list):
            emb = v.get("embedding")
            cnt = int(v.get("count") or 0)
        elif isinstance(v, list):
            emb = v
            cnt = 1
        else:
            continue
        try:
            vec = np.array([float(x) for x in emb], dtype=np.float32)
        except Exception:
            continue
        if vec.size == 0:
            continue
        # Ensure unit vector (cosine)
        n = float(np.linalg.norm(vec))
        if n > 0:
            vec = vec / n
        record = {"count": max(cnt, 1), "embedding": vec.tolist()}
        prev = out.get(cat)
        if prev is None:
            out[cat] = record
            continue
        try:
            prev_cnt = int(prev.get("count") or 1)
            prev_vec = np.array([float(x) for x in (prev.get("embedding") or [])], dtype=np.float32)
            if prev_vec.size == vec.size and prev_cnt > 0:
                merged = (prev_vec * prev_cnt + vec * int(record["count"])) / float(prev_cnt + int(record["count"]))
                mn = float(np.linalg.norm(merged))
                if mn > 0:
                    merged = merged / mn
                out[cat] = {"count": prev_cnt + int(record["count"]), "embedding": merged.tolist()}
            elif prev_cnt < int(record["count"]):
                out[cat] = record
        except Exception:
            if int(prev.get("count") or 1) < int(record["count"]):
                out[cat] = record
    return out

PROTOTYPES = _load_prototypes()

def _load_people_db() -> dict:
    if not PEOPLE_FILE.exists():
        return {}
    try:
        raw = json.loads(PEOPLE_FILE.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        out = {}
        for name, v in raw.items():
            if not isinstance(name, str) or not name.strip():
                continue
            if not isinstance(v, dict):
                continue
            emb = v.get("embedding")
            cnt = int(v.get("count") or 0)
            if not isinstance(emb, list) or not emb:
                continue
            try:
                vec = np.array([float(x) for x in emb], dtype=np.float32)
            except Exception:
                continue
            n = float(np.linalg.norm(vec))
            if n > 0:
                vec = vec / n
            out[name.strip()] = {"count": max(cnt, 1), "embedding": vec.tolist()}
        return out
    except Exception:
        return {}

def _save_people_db(db: dict) -> None:
    try:
        _atomic_write_json(PEOPLE_FILE, db)
    except Exception:
        pass

PEOPLE_DB = _load_people_db()

# ---------------------------
# AI PROVIDERS
# ---------------------------

AI_SETTINGS_FILE = DATA_DIR / "ai_settings.json"

AI_PROVIDER_NONE = "none"
AI_PROVIDER_CLIP_LOCAL = "clip_local"

_AI_PROVIDER_DEFS = {
    AI_PROVIDER_NONE: {
        "label": "None (Heuristics Only)",
        "description": "No local model. Uses filename/metadata heuristics only.",
        "packages": [],
    },
    AI_PROVIDER_CLIP_LOCAL: {
        "label": "CLIP Local (torch + open_clip)",
        "description": "Local CLIP model using torch and open_clip_torch.",
        "packages": ["torch", "open_clip_torch"],
        "requirements_file": str(
            SCRIPT_DIR / "ai_backend" / "providers" / "clip_local" / "requirements.txt"
        ),
    },
}

AI_PROVIDER_ID = AI_PROVIDER_NONE
AI_MODEL_PROFILE_ID = "clip_vit_b32_openai"
_AI_MODEL_PROFILES = {
    "clip_vit_b32_openai": {
        "id": "clip_vit_b32_openai",
        "label": "ViT-B/32 (Fast)",
        "description": "Fastest local CLIP option. Good default for CPU.",
        "model_name": "ViT-B-32",
        "model_pretrained": "openai",
    },
    "clip_vit_b16_openai": {
        "id": "clip_vit_b16_openai",
        "label": "ViT-B/16 (Balanced)",
        "description": "Better accuracy than B/32 with moderate extra runtime.",
        "model_name": "ViT-B-16",
        "model_pretrained": "openai",
    },
    "clip_vit_l14_openai": {
        "id": "clip_vit_l14_openai",
        "label": "ViT-L/14 (Higher Accuracy)",
        "description": "Heavier model; highest quality of these defaults.",
        "model_name": "ViT-L-14",
        "model_pretrained": "openai",
    },
}
_AI_WORKER_PROC = None
_AI_WORKER_PROVIDER_ID = None
_AI_WORKER_STATE_TOKEN = None
_AI_WORKER_LOCK = threading.Lock()


def _provider_runtime_dir(provider_id: str) -> Path:
    return DATA_DIR / "runtimes" / provider_id


def _provider_runtime_venv_dir(provider_id: str) -> Path:
    return _provider_runtime_dir(provider_id) / "venv"


def _provider_runtime_python(provider_id: str) -> Path:
    venv_dir = _provider_runtime_venv_dir(provider_id)
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _clip_worker_script_path() -> Path:
    return _provider_runtime_dir(AI_PROVIDER_CLIP_LOCAL) / "clip_worker.py"


def _clip_worker_source() -> str:
    # Standalone worker script so bundled apps can run provider runtime independent of project files.
    return r'''import json
import os
import sys
import random
from pathlib import Path

import numpy as np

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")

try:
    import torch  # type: ignore
    import open_clip  # type: ignore
    from PIL import Image, ImageFile  # type: ignore
    try:
        import pillow_heif  # type: ignore
        pillow_heif.register_heif_opener()
    except Exception:
        pass
except Exception as e:
    print(json.dumps({"ok": False, "error": f"import_error: {e}"}), flush=True)
    raise

ImageFile.LOAD_TRUNCATED_IMAGES = True

# Prefer deterministic inference behavior for repeatability.
try:
    random.seed(0)
except Exception:
    pass
try:
    np.random.seed(0)
except Exception:
    pass
try:
    torch.manual_seed(0)
except Exception:
    pass
try:
    torch.use_deterministic_algorithms(True, warn_only=True)
except Exception:
    pass
try:
    torch.set_num_threads(1)
except Exception:
    pass
try:
    torch.set_num_interop_threads(1)
except Exception:
    pass
try:
    if hasattr(torch.backends, "cudnn"):
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False
except Exception:
    pass
try:
    if hasattr(torch.backends, "cuda") and hasattr(torch.backends.cuda, "matmul"):
        torch.backends.cuda.matmul.allow_tf32 = False
except Exception:
    pass

MODEL = None
PREPROCESS = None
TOKENIZER = None
TEXT_FEATURES = None
PROTO_FEATURES = None
PROTO_MASK = None
MODEL_DEVICE = None
CATEGORIES = []
PROTOTYPES = {}
MODEL_NAME = "ViT-B-32"
MODEL_PRETRAINED = "openai"
MODEL_CACHE_DIR = None

IPHONE_SCREENSHOT_SIZES = {
    (640, 1136), (750, 1334), (828, 1792), (1125, 2436),
    (1242, 2688), (1170, 2532), (1284, 2778), (1179, 2556), (1290, 2796),
}


def _prompts_for_category(cat):
    c = (cat or "").strip().lower()
    if not c:
        return []
    if c == "family photo":
        return [
            "a family photo", "a photo of a person", "a close-up portrait photo of a person",
            "a portrait of a man", "a portrait of a woman", "a photo of people smiling",
            "a candid photo of a person indoors",
        ]
    if c == "selfie":
        return [
            "a selfie", "a selfie photo of a person", "a front camera selfie",
            "a selfie of a person indoors", "a mirror selfie",
        ]
    if c == "pet":
        return [
            "a photo of a pet", "a photo of a dog", "a photo of a cat",
            "a close-up photo of a dog", "a close-up photo of a cat", "a photo of an animal",
        ]
    if c == "shopping":
        return [
            "a photo of products on shelves in a store", "a photo of items on store shelves",
            "a photo of a grocery store aisle", "a photo of a retail store aisle",
            "a photo of a product display in a store", "a photo taken while shopping in a store",
            "a photo of packaged products on shelves", "a photo of a store shelf with items for sale",
        ]
    if c == "facebook download":
        return [
            "a photo downloaded from Facebook", "a photo saved from the Facebook app",
            "an image from a Facebook post", "a photo from a Facebook feed",
            "a re-shared image from Facebook", "a low quality compressed image from social media",
        ]
    if c == "iphone screenshot":
        return [
            "an iPhone screenshot", "a screenshot of an iPhone home screen with app icons",
            "a screenshot of an iPhone app with a status bar",
            "a screenshot of a mobile app interface on iOS",
            "a screenshot of a phone screen with iOS UI",
        ]
    if c == "political meme":
        return [
            "a political meme", "a meme about politics",
            "a political meme with text over an image",
            "a screenshot of a political post on social media",
            "a political infographic shared online", "a campaign or election meme",
        ]
    if c == "political cartoon":
        return [
            "a political cartoon", "an editorial cartoon about politics",
            "a political cartoon drawing", "a satirical political cartoon",
            "a comic strip about politics", "a single-panel political cartoon",
        ]
    if c == "screenshot":
        return [
            "a screenshot", "a screenshot of a phone screen", "a screenshot of a computer screen",
            "a screenshot of an app interface", "a screenshot with lots of text",
            "a screen capture of a website or app",
        ]
    if c == "document":
        return [
            "a photo of a document", "a photo of printed text on paper", "a scanned document",
            "a photo of a form", "a photo of a receipt", "a photo of a letter",
            "a photo of a page of text",
        ]
    if c == "food":
        return [
            "a photo of food", "a photo of a meal on a plate",
            "a photo of a restaurant dish", "a close-up photo of food", "a photo of a drink",
        ]
    if c == "car":
        return [
            "a photo of a car", "a photo of a vehicle", "a photo of a car interior",
            "a photo of a car dashboard", "a photo of a parked car outdoors",
        ]
    if c == "landscape":
        return [
            "a landscape photo", "a photo of mountains", "a photo of the ocean",
            "a scenic outdoor landscape", "a wide outdoor scenery photo",
        ]
    if c == "outdoor structure":
        return [
            "a photo of an outdoor structure", "a photo of a wooden deck",
            "a photo of a porch or deck railing", "a photo of a fence or railing outdoors",
            "a photo of a patio, deck, or backyard structure",
        ]
    if c == "outdoor photo":
        return [
            "an outdoor photo", "a photo taken outside", "a photo of people outdoors",
            "a photo outside in daylight", "an outdoor snapshot",
        ]
    if c == "indoor photo":
        return [
            "an indoor photo", "a photo taken inside", "a photo of people indoors",
            "a photo inside a room", "an indoor snapshot",
        ]
    return [f"a photo of {cat}", f"an image of {cat}", f"a snapshot of {cat}"]


def _safe_list(v):
    return v if isinstance(v, list) else []


def _refresh_text_features():
    global TEXT_FEATURES
    if MODEL is None or TOKENIZER is None or not CATEGORIES:
        TEXT_FEATURES = None
        return
    rows = []
    for cat in CATEGORIES:
        ps = _prompts_for_category(cat) or [cat]
        tokens = TOKENIZER(ps).to(MODEL_DEVICE)
        with torch.no_grad():
            feats = MODEL.encode_text(tokens)
        feats = feats / feats.norm(dim=-1, keepdim=True).clamp(min=1e-12)
        v = feats.mean(dim=0, keepdim=True)
        v = v / v.norm(dim=-1, keepdim=True).clamp(min=1e-12)
        rows.append(v)
    TEXT_FEATURES = torch.cat(rows, dim=0)


def _refresh_proto_features():
    global PROTO_FEATURES, PROTO_MASK
    if TEXT_FEATURES is None:
        PROTO_FEATURES = None
        PROTO_MASK = None
        return
    dim = int(TEXT_FEATURES.shape[-1])
    embs = []
    mask = []
    for c in CATEGORIES:
        p = PROTOTYPES.get(c)
        vec = None
        if isinstance(p, dict) and isinstance(p.get("embedding"), list):
            try:
                vec = np.array([float(x) for x in p["embedding"]], dtype=np.float32)
            except Exception:
                vec = None
        if vec is not None and vec.size == dim:
            n = float(np.linalg.norm(vec))
            if n > 0:
                vec = vec / n
            embs.append(vec)
            mask.append(True)
        else:
            embs.append(np.zeros((dim,), dtype=np.float32))
            mask.append(False)
    arr = np.stack(embs, axis=0)
    t = torch.from_numpy(arr).to(MODEL_DEVICE)
    t = t / t.norm(dim=-1, keepdim=True).clamp(min=1e-12)
    PROTO_FEATURES = t
    PROTO_MASK = torch.tensor(mask, dtype=torch.bool, device=MODEL_DEVICE)


def _looks_like_iphone_screenshot(image_path, pil_img):
    try:
        name = os.path.basename(image_path or "").lower()
        if "screenshot" in name or "screen shot" in name:
            return True
        ext = os.path.splitext(name)[1]
        if ext not in (".png", ".jpg", ".jpeg", ".heic", ".heif"):
            return False
        if pil_img is None:
            return False
        w, h = pil_img.size
        return (w, h) in IPHONE_SCREENSHOT_SIZES or (h, w) in IPHONE_SCREENSHOT_SIZES
    except Exception:
        return False


def _looks_like_facebook_download(image_path, _pil_img):
    try:
        name = os.path.basename(image_path or "").lower()
        if name.startswith("fb_img_") or name.startswith("fbimg_") or name.startswith("facebook_"):
            return True
        if "facebook" in name:
            return True
        if "facebook" in (image_path or "").lower():
            return True
        return False
    except Exception:
        return False


def _load_image(path):
    img = Image.open(path)
    return img.convert("RGB")


def _compute_features(pil_img, image_path):
    image_tensor = PREPROCESS(pil_img).unsqueeze(0).to(MODEL_DEVICE)
    with torch.no_grad():
        img_features = MODEL.encode_image(image_tensor)
    img_features = img_features / img_features.norm(dim=-1, keepdim=True).clamp(min=1e-12)

    sims_text = (img_features @ TEXT_FEATURES.T)[0]
    combined = sims_text

    if PROTO_FEATURES is not None and PROTO_MASK is not None and bool(PROTO_MASK.any().item()):
        sims_proto = (img_features @ PROTO_FEATURES.T)[0]
        proto_w = 0.70
        combined = sims_text.clone()
        combined[PROTO_MASK] = (1.0 - proto_w) * sims_text[PROTO_MASK] + proto_w * sims_proto[PROTO_MASK]

    try:
        if image_path:
            if "iphone screenshot" in CATEGORIES and not _looks_like_iphone_screenshot(image_path, pil_img):
                idx = CATEGORIES.index("iphone screenshot")
                combined[idx] = -1e9
            if "facebook download" in CATEGORIES and not _looks_like_facebook_download(image_path, pil_img):
                idx = CATEGORIES.index("facebook download")
                combined[idx] = -1e9
    except Exception:
        pass

    return combined, img_features


def _op_load(req):
    global MODEL, PREPROCESS, TOKENIZER, MODEL_DEVICE
    global CATEGORIES, PROTOTYPES, MODEL_NAME, MODEL_PRETRAINED, MODEL_CACHE_DIR

    MODEL_NAME = str(req.get("model_name") or MODEL_NAME)
    MODEL_PRETRAINED = str(req.get("model_pretrained") or MODEL_PRETRAINED)
    MODEL_CACHE_DIR = str(req.get("model_cache_dir") or "")
    CATEGORIES = _safe_list(req.get("categories"))
    PROTOTYPES = req.get("prototypes") if isinstance(req.get("prototypes"), dict) else {}

    if MODEL_CACHE_DIR:
        try:
            Path(MODEL_CACHE_DIR).mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    MODEL_DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
    # Use validation/inference preprocessing (deterministic), not training augmentation.
    MODEL, _, PREPROCESS = open_clip.create_model_and_transforms(
        MODEL_NAME,
        pretrained=MODEL_PRETRAINED,
        device=MODEL_DEVICE,
        precision="fp32",
        force_quick_gelu=True,
        cache_dir=MODEL_CACHE_DIR if MODEL_CACHE_DIR else None,
    )
    MODEL.eval()
    TOKENIZER = open_clip.get_tokenizer(MODEL_NAME)
    _refresh_text_features()
    _refresh_proto_features()
    return {"ok": True, "message": f"clip worker ready on {MODEL_DEVICE}", "device": MODEL_DEVICE}


def _op_sync(req):
    global CATEGORIES, PROTOTYPES
    CATEGORIES = _safe_list(req.get("categories"))
    PROTOTYPES = req.get("prototypes") if isinstance(req.get("prototypes"), dict) else {}
    _refresh_text_features()
    _refresh_proto_features()
    return {"ok": True}


def _op_predict(req):
    image_path = str(req.get("image_path") or "")
    if not image_path:
        return {"ok": False, "error": "missing image_path"}
    pil_img = _load_image(image_path)
    combined, img_features = _compute_features(pil_img, image_path)
    k = min(3, int(combined.numel()))
    vals, idxs = torch.topk(combined, k=k, largest=True, sorted=True)
    topk = []
    for v, i in zip(vals.tolist(), idxs.tolist()):
        topk.append({"category": CATEGORIES[int(i)], "score": float(v)})
    best_idx = int(idxs[0].item())
    best_score = float(vals[0].item())
    emb_np = img_features[0].detach().float().cpu().numpy().tolist()
    return {
        "ok": True,
        "category": CATEGORIES[best_idx],
        "score": best_score,
        "embedding": emb_np,
        "topk": topk,
    }


def _op_rank(req):
    image_path = str(req.get("image_path") or "")
    topk = int(req.get("topk") or 3)
    if not image_path:
        return {"ok": False, "error": "missing image_path"}
    pil_img = _load_image(image_path)
    combined, _ = _compute_features(pil_img, image_path)
    k = min(max(1, topk), int(combined.numel()))
    vals, idxs = torch.topk(combined, k=k, largest=True, sorted=True)
    out = []
    for v, i in zip(vals.tolist(), idxs.tolist()):
        out.append({"category": CATEGORIES[int(i)], "score": float(v)})
    return {"ok": True, "topk": out}


OPS = {
    "load": _op_load,
    "sync": _op_sync,
    "predict": _op_predict,
    "rank": _op_rank,
}


def main():
    for line in sys.stdin:
        line = (line or "").strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            op = str(req.get("op") or "")
            fn = OPS.get(op)
            if fn is None:
                resp = {"ok": False, "error": f"unknown op: {op}"}
            else:
                resp = fn(req)
        except Exception as e:
            resp = {"ok": False, "error": str(e)}
        print(json.dumps(resp, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
'''


def _write_clip_worker_script() -> Path:
    script_path = _clip_worker_script_path()
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(_clip_worker_source(), encoding="utf-8")
    return script_path


def _bootstrap_python_candidates() -> list[list[str]]:
    out = []
    env_py = (os.environ.get("MEDIASORTER_RUNTIME_PYTHON") or "").strip()
    if env_py:
        out.append([env_py])
    py_launcher = shutil.which("py")
    if py_launcher:
        out.append([py_launcher, "-3.12"])
        out.append([py_launcher, "-3"])
    py_bin = shutil.which("python")
    if py_bin:
        out.append([py_bin])
    exe = Path(sys.executable)
    if exe.exists() and "python" in exe.name.lower():
        out.append([str(exe)])
    # de-dup while preserving order
    seen = set()
    deduped = []
    for cmd in out:
        key = tuple(cmd)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cmd)
    return deduped


def _probe_python(cmd_prefix: list[str]) -> bool:
    try:
        p = subprocess.run(
            [*cmd_prefix, "-c", "import sys; print(sys.version_info[0])"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return p.returncode == 0 and p.stdout.strip() == "3"
    except Exception:
        return False


def _resolve_bootstrap_python() -> list[str] | None:
    for cmd in _bootstrap_python_candidates():
        if _probe_python(cmd):
            return cmd
    return None


def _run_with_streaming(command: list[str], status_cb=None, cwd: str | None = None) -> int:
    if callable(status_cb):
        status_cb(" ".join(command))
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
    )
    if proc.stdout is not None:
        for line in proc.stdout:
            line = line.rstrip()
            if line and callable(status_cb):
                status_cb(line)
    return int(proc.wait())


def _install_clip_provider_runtime(status_cb=None) -> tuple[bool, str]:
    runtime_dir = _provider_runtime_dir(AI_PROVIDER_CLIP_LOCAL)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    venv_dir = _provider_runtime_venv_dir(AI_PROVIDER_CLIP_LOCAL)
    runtime_python = _provider_runtime_python(AI_PROVIDER_CLIP_LOCAL)

    if not runtime_python.exists():
        bootstrap = _resolve_bootstrap_python()
        if bootstrap is None:
            return False, (
                "Unable to find a Python interpreter to create provider runtime. "
                "Install Python 3.12 or set MEDIASORTER_RUNTIME_PYTHON."
            )
        if callable(status_cb):
            status_cb("Creating clip_local runtime venv...")
        rc = _run_with_streaming([*bootstrap, "-m", "venv", str(venv_dir)], status_cb=status_cb)
        if rc != 0 or not runtime_python.exists():
            return False, f"Failed creating runtime venv (exit {rc})."

    req_file = (
        SCRIPT_DIR / "ai_backend" / "providers" / "clip_local" / "requirements.txt"
    ).resolve()
    if not req_file.exists():
        return False, f"Missing requirements file: {req_file}"

    if callable(status_cb):
        status_cb("Upgrading runtime packaging tools...")
    rc = _run_with_streaming(
        [str(runtime_python), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"],
        status_cb=status_cb,
    )
    if rc != 0:
        return False, f"Failed upgrading runtime pip tools (exit {rc})."

    if callable(status_cb):
        status_cb("Installing clip_local runtime packages...")
    rc = _run_with_streaming(
        [str(runtime_python), "-m", "pip", "install", "--upgrade", "-r", str(req_file)],
        status_cb=status_cb,
    )
    if rc != 0:
        return False, f"Failed installing clip_local packages (exit {rc})."

    if not _runtime_clip_imports_ok():
        return False, "Runtime packages installed, but torch/open_clip/pillow_heif imports still fail."

    _write_clip_worker_script()
    return True, f"{get_ai_provider_display_name(AI_PROVIDER_CLIP_LOCAL)} installed."


def _detect_default_ai_provider() -> str:
    # Default to lightweight mode unless explicitly selected in settings.
    return AI_PROVIDER_NONE


def _default_ai_model_profile_id() -> str:
    return "clip_vit_b32_openai"


def _resolve_ai_model_profile(model_profile_id: str | None = None) -> dict:
    pid = (model_profile_id or AI_MODEL_PROFILE_ID or _default_ai_model_profile_id()).strip()
    profile = _AI_MODEL_PROFILES.get(pid)
    if profile is None:
        profile = _AI_MODEL_PROFILES[_default_ai_model_profile_id()]
    return dict(profile)


def _apply_ai_model_profile_to_runtime() -> None:
    global AI_MODEL_PROFILE_ID, MODEL_NAME, MODEL_PRETRAINED
    profile = _resolve_ai_model_profile(AI_MODEL_PROFILE_ID)
    AI_MODEL_PROFILE_ID = str(profile.get("id") or _default_ai_model_profile_id())
    if "MODEL_NAME" in globals():
        MODEL_NAME = str(profile.get("model_name") or MODEL_NAME)
    if "MODEL_PRETRAINED" in globals():
        MODEL_PRETRAINED = str(profile.get("model_pretrained") or MODEL_PRETRAINED)


def _load_ai_provider_settings() -> None:
    global AI_PROVIDER_ID, AI_MODEL_PROFILE_ID
    provider_id = _detect_default_ai_provider()
    model_profile_id = _default_ai_model_profile_id()
    try:
        if AI_SETTINGS_FILE.exists():
            data = json.loads(AI_SETTINGS_FILE.read_text(encoding="utf-8"))
            candidate = (data.get("provider_id") or "").strip()
            if candidate in _AI_PROVIDER_DEFS:
                provider_id = candidate
            model_candidate = (data.get("model_profile_id") or "").strip()
            if model_candidate in _AI_MODEL_PROFILES:
                model_profile_id = model_candidate
    except Exception:
        pass
    AI_PROVIDER_ID = provider_id
    AI_MODEL_PROFILE_ID = model_profile_id
    _apply_ai_model_profile_to_runtime()


def _save_ai_provider_settings() -> None:
    try:
        _atomic_write_json(
            AI_SETTINGS_FILE,
            {"provider_id": AI_PROVIDER_ID, "model_profile_id": AI_MODEL_PROFILE_ID},
        )
    except Exception:
        pass


def get_ai_provider_options() -> list[dict]:
    out = []
    for provider_id, meta in _AI_PROVIDER_DEFS.items():
        out.append(
            {
                "id": provider_id,
                "label": str(meta.get("label") or provider_id),
                "description": str(meta.get("description") or ""),
            }
        )
    return out


def get_ai_provider_id() -> str:
    return AI_PROVIDER_ID


def get_ai_provider_display_name(provider_id: str | None = None) -> str:
    pid = provider_id or AI_PROVIDER_ID
    meta = _AI_PROVIDER_DEFS.get(pid, {})
    return str(meta.get("label") or pid)


def get_ai_model_options(provider_id: str | None = None) -> list[dict]:
    pid = provider_id or AI_PROVIDER_ID
    if pid != AI_PROVIDER_CLIP_LOCAL:
        return []
    out = []
    for model_profile_id, meta in _AI_MODEL_PROFILES.items():
        out.append(
            {
                "id": model_profile_id,
                "label": str(meta.get("label") or model_profile_id),
                "description": str(meta.get("description") or ""),
                "model_name": str(meta.get("model_name") or ""),
                "model_pretrained": str(meta.get("model_pretrained") or ""),
            }
        )
    return out


def get_ai_model_id() -> str:
    return AI_MODEL_PROFILE_ID


def get_ai_model_display_name(model_profile_id: str | None = None) -> str:
    profile = _resolve_ai_model_profile(model_profile_id)
    return str(profile.get("label") or profile.get("id") or "Unknown")


def _runtime_clip_imports_ok() -> bool:
    runtime_python = _provider_runtime_python(AI_PROVIDER_CLIP_LOCAL)
    if not runtime_python.exists():
        return False
    try:
        proc = subprocess.run(
            [
                str(runtime_python),
                "-c",
                "import torch, open_clip, pillow_heif; pillow_heif.register_heif_opener(); print('ok')",  # noqa: E702
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return proc.returncode == 0 and "ok" in (proc.stdout or "")
    except Exception:
        return False


def is_ai_provider_installed(provider_id: str | None = None) -> bool:
    pid = provider_id or AI_PROVIDER_ID
    if pid == AI_PROVIDER_NONE:
        return True
    if pid == AI_PROVIDER_CLIP_LOCAL:
        return _runtime_clip_imports_ok()
    return False


def _terminate_ai_worker() -> None:
    global _AI_WORKER_PROC, _AI_WORKER_PROVIDER_ID, _AI_WORKER_STATE_TOKEN
    p = _AI_WORKER_PROC
    _AI_WORKER_PROC = None
    _AI_WORKER_PROVIDER_ID = None
    _AI_WORKER_STATE_TOKEN = None
    if p is None:
        return
    try:
        p.terminate()
    except Exception:
        pass
    try:
        p.wait(timeout=5)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass


atexit.register(_terminate_ai_worker)


def _reset_model_state(clear_error: bool = True) -> None:
    global MODEL_DEVICE, MODEL, PREPROCESS, TOKENIZER
    global TEXT_FEATURES, PROTO_FEATURES, PROTO_MASK
    global _TORCH, _OPEN_CLIP
    global _MODEL_READY, _MODEL_LOAD_ERROR
    MODEL_DEVICE = None
    MODEL = None
    PREPROCESS = None
    TOKENIZER = None
    TEXT_FEATURES = None
    PROTO_FEATURES = None
    PROTO_MASK = None
    _TORCH = None
    _OPEN_CLIP = None
    _MODEL_READY = False
    _terminate_ai_worker()
    if clear_error:
        _MODEL_LOAD_ERROR = None


def set_ai_provider(provider_id: str) -> None:
    global AI_PROVIDER_ID
    pid = (provider_id or "").strip()
    if pid not in _AI_PROVIDER_DEFS:
        raise ValueError(f"Unknown AI provider: {provider_id}")
    if pid == AI_PROVIDER_ID:
        return
    AI_PROVIDER_ID = pid
    _save_ai_provider_settings()
    _reset_model_state(clear_error=True)


def set_ai_model_profile(model_profile_id: str) -> None:
    global AI_MODEL_PROFILE_ID
    mid = (model_profile_id or "").strip()
    if mid not in _AI_MODEL_PROFILES:
        raise ValueError(f"Unknown AI model profile: {model_profile_id}")
    if mid == AI_MODEL_PROFILE_ID:
        return
    AI_MODEL_PROFILE_ID = mid
    _apply_ai_model_profile_to_runtime()
    _save_ai_provider_settings()
    _reset_model_state(clear_error=True)


def install_ai_provider(provider_id: str | None = None, status_cb=None) -> tuple[bool, str]:
    pid = (provider_id or AI_PROVIDER_ID).strip()
    if pid not in _AI_PROVIDER_DEFS:
        return False, f"Unknown AI provider: {pid}"
    if pid == AI_PROVIDER_NONE:
        return True, "No installation required for this provider."
    if is_ai_provider_installed(pid):
        return True, f"{get_ai_provider_display_name(pid)} is already installed."

    if pid == AI_PROVIDER_CLIP_LOCAL:
        return _install_clip_provider_runtime(status_cb=status_cb)

    provider_meta = dict(_AI_PROVIDER_DEFS.get(pid, {}) or {})
    requirements_file = (provider_meta.get("requirements_file") or "").strip()
    packages = list(provider_meta.get("packages") or [])
    cmd = None
    if requirements_file:
        req_path = Path(requirements_file)
        if req_path.exists():
            cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "-r", str(req_path)]
    if cmd is None:
        if not packages:
            return False, f"No package list configured for provider: {pid}"
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", *packages]
    try:
        if callable(status_cb):
            status_cb("Installing provider packages...")
            status_cb(" ".join(cmd))
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.stdout is not None:
            for line in proc.stdout:
                line = line.rstrip()
                if line and callable(status_cb):
                    status_cb(line)
        rc = int(proc.wait())
    except Exception as e:
        return False, f"Provider install failed: {e}"

    if rc != 0:
        return False, f"Provider install command failed with exit code {rc}."
    if not is_ai_provider_installed(pid):
        return False, "Install command completed, but provider imports are still missing."
    return True, f"{get_ai_provider_display_name(pid)} installed."


_load_ai_provider_settings()

# ---------------------------
# CLIP MODEL (loaded lazily in background)
# ---------------------------

MODEL_NAME = "ViT-B-32"
MODEL_PRETRAINED = "openai"
MODEL_CACHE_DIR = DATA_DIR / "models" / "open_clip"
_apply_ai_model_profile_to_runtime()
MODEL_DEVICE = None
MODEL = None
PREPROCESS = None
TOKENIZER = None
TEXT_FEATURES = None  # torch.Tensor [num_categories, dim]
PROTO_FEATURES = None  # torch.Tensor [num_categories, dim] for learned prototypes
PROTO_MASK = None  # torch.BoolTensor [num_categories]
_TORCH = None
_OPEN_CLIP = None
_MODEL_READY = False
_MODEL_LOAD_ERROR = None
_MODEL_LOCK = threading.Lock()
_INFER_LOCK = threading.Lock()
_FACE_CASCADE = None
_FACE_DETECTOR = None
_FACE_RECOGNIZER = None
_FACE_MODEL_LOCK = threading.Lock()

FACE_MODELS_DIR = DATA_DIR / "models" / "faces"
YUNET_MODEL = FACE_MODELS_DIR / "face_detection_yunet_2023mar.onnx"
SFACE_MODEL = FACE_MODELS_DIR / "face_recognition_sface_2021dec.onnx"
YUNET_URL = "https://raw.githubusercontent.com/opencv/opencv_zoo/master/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
SFACE_URL = "https://raw.githubusercontent.com/opencv/opencv_zoo/master/models/face_recognition_sface/face_recognition_sface_2021dec.onnx"

# ---------------------------
# HELPER FUNCTIONS
# ---------------------------

def hash_image(path):
    try:
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except:
        return None

def convert_video(input_path, output_path):
    if not os.path.exists(HANDBRAKE_PATH):
        raise FileNotFoundError(f"HandBrakeCLI not found at: {HANDBRAKE_PATH}")
    command = [
        HANDBRAKE_PATH,
        "-i", input_path,
        "-o", output_path,
        "--preset", "Fast 1080p30"
    ]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def _unique_dest_path(dest_dir: str, filename: str) -> str:
    """Return a destination path that won't overwrite an existing file."""
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, filename)
    if not os.path.exists(dest):
        return dest
    name, ext = os.path.splitext(filename)
    idx = 1
    while True:
        cand = os.path.join(dest_dir, f"{name}_{idx}{ext}")
        if not os.path.exists(cand):
            return cand
        idx += 1

def load_image_for_ai(path):
    """Load image robustly using multiple libraries."""
    lower_path = str(path or "").lower()
    # --- OpenCV first ---
    if HAS_CV2:
        try:
            data = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
            if data is not None:
                data = cv2.cvtColor(data, cv2.COLOR_BGR2RGB)
                return Image.fromarray(data)
        except:
            pass

    # --- Pillow ---
    try:
        img = Image.open(path)
        img = img.convert("RGB")
        return img
    except:
        pass

    # --- Direct HEIC decode fallback (works even if Pillow opener registration is missing) ---
    if lower_path.endswith((".heic", ".heif")):
        try:
            _init_heic_support()
            import pillow_heif  # type: ignore

            hf = pillow_heif.open_heif(path)
            img = hf.to_pillow()
            if img is not None:
                return img.convert("RGB")
        except Exception:
            pass

    # --- imageio fallback ---
    try:
        img_array = iio.imread(path)
        return Image.fromarray(img_array).convert("RGB")
    except:
        pass

    # Unsupported or corrupt
    return None

def _largest_face_fraction(pil_img) -> float:
    """Return largest detected face area / image area, or 0.0 if none/unsupported."""
    global _FACE_CASCADE
    if not HAS_CV2:
        return 0.0
    if pil_img is None:
        return 0.0

    try:
        # Lazy init: Haar cascade ships with OpenCV wheels.
        if _FACE_CASCADE is None:
            try:
                cascade_path = os.path.join(getattr(cv2.data, "haarcascades", ""), "haarcascade_frontalface_default.xml")
                _FACE_CASCADE = cv2.CascadeClassifier(cascade_path)
            except Exception:
                _FACE_CASCADE = False
        if _FACE_CASCADE is False:
            return 0.0

        arr = np.asarray(pil_img.convert("RGB"))
        if arr.size == 0:
            return 0.0
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)

        faces = _FACE_CASCADE.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            flags=cv2.CASCADE_SCALE_IMAGE,
            minSize=(30, 30),
        )
        if faces is None or len(faces) == 0:
            return 0.0

        img_area = float(pil_img.size[0] * pil_img.size[1]) or 1.0
        max_area = 0.0
        for (x, y, w, h) in faces:
            a = float(w * h)
            if a > max_area:
                max_area = a
        return max_area / img_area
    except Exception:
        return 0.0

def _download_file(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    urllib.request.urlretrieve(url, tmp)  # nosec - user-controlled URLs not accepted
    tmp.replace(dest)

def _ensure_face_models() -> None:
    """Ensure YuNet (detector) + SFace (recognizer) models are present and loaded."""
    global _FACE_DETECTOR, _FACE_RECOGNIZER
    if not HAS_CV2:
        raise RuntimeError("OpenCV not available")
    if _FACE_DETECTOR is not None and _FACE_RECOGNIZER is not None:
        return

    with _FACE_MODEL_LOCK:
        if _FACE_DETECTOR is not None and _FACE_RECOGNIZER is not None:
            return

        try:
            FACE_MODELS_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        if not YUNET_MODEL.exists():
            _log_line(f"[faces] Downloading YuNet model to {YUNET_MODEL}")
            _download_file(YUNET_URL, YUNET_MODEL)
        if not SFACE_MODEL.exists():
            _log_line(f"[faces] Downloading SFace model to {SFACE_MODEL}")
            _download_file(SFACE_URL, SFACE_MODEL)

        # Create detector with a default size; will be overridden per-image via setInputSize.
        _FACE_DETECTOR = cv2.FaceDetectorYN.create(str(YUNET_MODEL), "", (320, 320), 0.9, 0.3, 5000)
        _FACE_RECOGNIZER = cv2.FaceRecognizerSF.create(str(SFACE_MODEL), "")

def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    try:
        aa = float(np.linalg.norm(a))
        bb = float(np.linalg.norm(b))
        if aa <= 0 or bb <= 0:
            return -1.0
        return float(np.dot(a, b) / (aa * bb))
    except Exception:
        return -1.0

def _extract_face_embeddings(image_path: str, pil_img=None, max_faces: int = 2):
    """
    Return list of dicts: [{"embedding": np.ndarray (unit), "bbox": (x,y,w,h)}] in original image coords.
    """
    if not HAS_CV2:
        return []
    if pil_img is None:
        pil_img = load_image_for_ai(image_path)
    if pil_img is None:
        return []

    # Quick filter (fast): if Haar sees no face-ish region, skip the expensive DNN step.
    try:
        if _largest_face_fraction(pil_img) <= 0.0:
            return []
    except Exception:
        pass

    try:
        _ensure_face_models()
    except Exception as e:
        _log_line(f"[faces][error] {e}")
        return []

    try:
        rgb = np.asarray(pil_img.convert("RGB"))
        if rgb.size == 0:
            return []
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        oh, ow = bgr.shape[:2]
        max_dim = max(ow, oh)
        scale = 1.0
        if max_dim > 800:
            scale = 800.0 / float(max_dim)
            nw = max(1, int(ow * scale))
            nh = max(1, int(oh * scale))
            bgr_small = cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_AREA)
        else:
            bgr_small = bgr
        h, w = bgr_small.shape[:2]

        det = _FACE_DETECTOR
        rec = _FACE_RECOGNIZER
        if det is None or rec is None:
            return []
        det.setInputSize((w, h))
        _, faces = det.detect(bgr_small)
        if faces is None or len(faces) == 0:
            return []

        # faces: [x,y,w,h,score, l0x,l0y,...] sort by area desc
        faces = np.asarray(faces, dtype=np.float32)
        areas = faces[:, 2] * faces[:, 3]
        order = np.argsort(-areas)
        out = []
        for idx in order[: max_faces * 3]:
            f = faces[int(idx)]
            x, y, ww, hh = float(f[0]), float(f[1]), float(f[2]), float(f[3])
            if ww < 50 or hh < 50:
                continue
            # Align/crop on the small image using the original face row.
            try:
                crop = rec.alignCrop(bgr_small, f)
                feat = rec.feature(crop)
                v = feat.flatten().astype(np.float32)
                n = float(np.linalg.norm(v))
                if n > 0:
                    v = v / n
            except Exception:
                continue

            # bbox in original coordinates
            if scale != 1.0:
                xo = int(max(0.0, x / scale))
                yo = int(max(0.0, y / scale))
                wwo = int(max(1.0, ww / scale))
                hho = int(max(1.0, hh / scale))
            else:
                xo, yo, wwo, hho = int(max(0.0, x)), int(max(0.0, y)), int(max(1.0, ww)), int(max(1.0, hh))

            out.append({"embedding": v, "bbox": (xo, yo, wwo, hho)})
            if len(out) >= max_faces:
                break
        return out
    except Exception:
        return []

def _parse_exif_datetime(s: str):
    if not s:
        return None
    s = str(s).strip()
    # Common EXIF form: "YYYY:MM:DD HH:MM:SS"
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass
    return None

def _image_datetime(image_path: str, pil_img):
    """Best-effort datetime for the media. Prefers EXIF, falls back to filesystem mtime."""
    # EXIF tags: DateTimeOriginal(36867), DateTimeDigitized(36868), DateTime(306)
    try:
        ex = pil_img.getexif() if pil_img is not None else None
        if ex:
            for tag in (36867, 36868, 306):
                dt = _parse_exif_datetime(ex.get(tag))
                if dt is not None:
                    return dt
    except Exception:
        pass

    # Fallback: mtime (pragmatic; not "in photo" but avoids huge Unknown buckets)
    try:
        ts = os.path.getmtime(image_path)
        return datetime.fromtimestamp(ts)
    except Exception:
        return None

def _image_year_month(image_path: str, pil_img):
    dt = _image_datetime(image_path, pil_img)
    if dt is None:
        return None, None
    try:
        return f"{dt.year:04d}", f"{dt.month:02d}"
    except Exception:
        return None, None

def _rational_to_float(x) -> float:
    try:
        if isinstance(x, tuple) and len(x) == 2:
            num, den = x
            return float(num) / float(den) if float(den) != 0 else 0.0
        return float(x)
    except Exception:
        return 0.0

def _gps_to_decimal(gps_info: dict):
    # gps_info keys per EXIF: 1 lat_ref, 2 lat, 3 lon_ref, 4 lon
    try:
        lat_ref = gps_info.get(1)
        lat = gps_info.get(2)
        lon_ref = gps_info.get(3)
        lon = gps_info.get(4)
        if not (lat_ref and lat and lon_ref and lon):
            return None

        def dms_to_deg(dms):
            d = _rational_to_float(dms[0])
            m = _rational_to_float(dms[1])
            s = _rational_to_float(dms[2])
            return d + (m / 60.0) + (s / 3600.0)

        lat_v = dms_to_deg(lat)
        lon_v = dms_to_deg(lon)
        if str(lat_ref).upper().startswith("S"):
            lat_v = -lat_v
        if str(lon_ref).upper().startswith("W"):
            lon_v = -lon_v
        return float(lat_v), float(lon_v)
    except Exception:
        return None

def _image_location_folder(image_path: str, pil_img) -> str | None:
    """Return a folder-friendly location label if GPS exists, else None."""
    try:
        ex = pil_img.getexif() if pil_img is not None else None
        if not ex:
            return None
        gps = ex.get(34853)
        if gps is None or not hasattr(gps, "get"):
            return None
        dec = _gps_to_decimal(gps)
        if not dec:
            return None
        lat, lon = dec
        # Round to reduce folder explosion; 2 decimals ~ 1km.
        lat_r = round(lat, 2)
        lon_r = round(lon, 2)
        return f"GPS_{lat_r:+.2f}_{lon_r:+.2f}".replace("+", "")
    except Exception:
        return None

def _structure_tokens(category: str, image_path: str, pil_img) -> dict:
    year, month = _image_year_month(image_path, pil_img)
    if year and month:
        yearmonth = f"{year}-{month}"
        yearmo = f"{year}{month}"
    else:
        yearmonth = None
        yearmo = None
    loc = _image_location_folder(image_path, pil_img)
    return {
        "category": category or "Uncategorized",
        "year": year,
        "month": month,
        "yearmonth": yearmonth,
        "yearmo": yearmo,
        "location": loc,
    }

def _render_structure(output_folder: str, pattern: str, tokens: dict) -> str:
    """
    Render a user-selected folder structure.

    Pattern is a path-like string using tokens such as:
      {category} {year} {month} {yearmonth} {yearmo} {location}
    Separators can be / or \\.
    """
    pat = (pattern or "").strip()
    if not pat:
        pat = "{category}"

    # Always include category somewhere so the sorter stays a sorter.
    if "{category}" not in pat:
        pat = pat.rstrip("/\\")
        pat = pat + "/{category}"

    # Normalize separators so users can type either.
    pat = pat.replace("\\", "/")
    segs = [s for s in pat.split("/") if s.strip()]

    def missing(name: str) -> bool:
        v = tokens.get(name)
        return v is None or v == ""

    def tok(name: str) -> str:
        # Category is always present; others may be missing and should simply be skipped.
        if name == "category":
            v = tokens.get("category")
            return str(v) if v else "Uncategorized"
        v = tokens.get(name)
        return "" if (v is None or v == "") else str(v)

    out_parts = [output_folder]
    for seg in segs:
        # If this segment references missing metadata, drop the segment entirely.
        refs = set()
        for name in ("category", "year", "month", "yearmonth", "yearmo", "location"):
            if ("{" + name + "}") in seg:
                refs.add(name)
        if "{gps}" in seg:
            refs.add("location")
        if "{date}" in seg:
            refs.add("yearmonth")
        if "{ym}" in seg:
            refs.add("yearmo")

        # Special handling: yearmonth/yearmo depend on year+month.
        if "yearmonth" in refs and (missing("year") or missing("month") or missing("yearmonth")):
            continue
        if "yearmo" in refs and (missing("year") or missing("month") or missing("yearmo")):
            continue

        skip = False
        for r in refs:
            if r != "category" and missing(r):
                skip = True
                break
        if skip:
            continue

        s = seg
        # Simple token substitution; repeated tokens are allowed.
        for name in ("category", "year", "month", "yearmonth", "yearmo", "location"):
            s = s.replace("{" + name + "}", tok(name))
        # Accept a few aliases to keep things intuitive.
        s = s.replace("{gps}", tok("location"))
        s = s.replace("{date}", tok("yearmonth"))
        s = s.replace("{ym}", tok("yearmo"))
        s = s.strip()
        s = s.strip(" _-.")
        if not s:
            continue
        out_parts.append(_safe_folder_name(s))

    # Ensure we always end up with a leaf folder.
    if len(out_parts) == 1:
        out_parts.append(_safe_folder_name(tok("category")))

    return os.path.join(*out_parts)

def _save_prototypes():
    try:
        payload = {
            "version": 1,
            "model": f"{MODEL_NAME}/{MODEL_PRETRAINED}",
            "prototypes": PROTOTYPES,
        }
        _atomic_write_json(PROTOTYPES_FILE, payload)
    except Exception:
        # best-effort; don't crash the app for persistence failures
        pass

def _refresh_text_features():
    global TEXT_FEATURES
    if get_ai_provider_id() == AI_PROVIDER_CLIP_LOCAL and _AI_WORKER_PROC is not None:
        try:
            _worker_sync_if_needed(force=True)
        except Exception:
            pass
        return
    # Called during model load; don't gate on _MODEL_READY.
    if MODEL is None or TOKENIZER is None or _TORCH is None:
        TEXT_FEATURES = None
        return
    if not CATEGORIES:
        TEXT_FEATURES = None
        return

    def prompts_for(cat: str) -> list[str]:
        c = (cat or "").strip().lower()
        if not c:
            return []

        if c == "family photo":
            return [
                "a family photo",
                "a photo of a person",
                "a close-up portrait photo of a person",
                "a portrait of a man",
                "a portrait of a woman",
                "a photo of people smiling",
                "a candid photo of a person indoors",
            ]

        if c == "selfie":
            return [
                "a selfie",
                "a selfie photo of a person",
                "a front camera selfie",
                "a selfie of a person indoors",
                "a mirror selfie",
            ]

        if c == "pet":
            return [
                "a photo of a pet",
                "a photo of a dog",
                "a photo of a cat",
                "a close-up photo of a dog",
                "a close-up photo of a cat",
                "a photo of an animal",
            ]

        # Make "shopping" strongly capture store shelves/aisles/product displays.
        if c == "shopping":
            return [
                "a photo of products on shelves in a store",
                "a photo of items on store shelves",
                "a photo of a grocery store aisle",
                "a photo of a retail store aisle",
                "a photo of a product display in a store",
                "a photo taken while shopping in a store",
                "a photo of packaged products on shelves",
                "a photo of a store shelf with items for sale",
            ]

        if c == "facebook download":
            return [
                "a photo downloaded from Facebook",
                "a photo saved from the Facebook app",
                "an image from a Facebook post",
                "a photo from a Facebook feed",
                "a re-shared image from Facebook",
                "a low quality compressed image from social media",
            ]

        if c == "iphone screenshot":
            return [
                "an iPhone screenshot",
                "a screenshot of an iPhone home screen with app icons",
                "a screenshot of an iPhone app with a status bar",
                "a screenshot of a mobile app interface on iOS",
                "a screenshot of a phone screen with iOS UI",
            ]

        if c == "political meme":
            return [
                "a political meme",
                "a meme about politics",
                "a political meme with text over an image",
                "a screenshot of a political post on social media",
                "a political infographic shared online",
                "a campaign or election meme",
            ]

        if c == "political cartoon":
            return [
                "a political cartoon",
                "an editorial cartoon about politics",
                "a political cartoon drawing",
                "a satirical political cartoon",
                "a comic strip about politics",
                "a single-panel political cartoon",
            ]

        if c == "screenshot":
            return [
                "a screenshot",
                "a screenshot of a phone screen",
                "a screenshot of a computer screen",
                "a screenshot of an app interface",
                "a screenshot with lots of text",
                "a screen capture of a website or app",
            ]

        if c == "document":
            return [
                "a photo of a document",
                "a photo of printed text on paper",
                "a scanned document",
                "a photo of a form",
                "a photo of a receipt",
                "a photo of a letter",
                "a photo of a page of text",
            ]

        if c == "food":
            return [
                "a photo of food",
                "a photo of a meal on a plate",
                "a photo of a restaurant dish",
                "a close-up photo of food",
                "a photo of a drink",
            ]

        if c == "car":
            return [
                "a photo of a car",
                "a photo of a vehicle",
                "a photo of a car interior",
                "a photo of a car dashboard",
                "a photo of a parked car outdoors",
            ]

        if c == "landscape":
            return [
                "a landscape photo",
                "a photo of mountains",
                "a photo of the ocean",
                "a scenic outdoor landscape",
                "a wide outdoor scenery photo",
            ]

        if c == "outdoor photo":
            return [
                "an outdoor photo",
                "a photo taken outside",
                "a photo of people outdoors",
                "a photo outside in daylight",
                "an outdoor snapshot",
            ]

        if c == "indoor photo":
            return [
                "an indoor photo",
                "a photo taken inside",
                "a photo of people indoors",
                "a photo inside a room",
                "an indoor snapshot",
            ]

        # Generic prompts for all other categories (including user-defined ones).
        # Multiple prompt variants usually improves CLIP zero-shot robustness.
        return [
            f"a photo of {cat}",
            f"an image of {cat}",
            f"a snapshot of {cat}",
        ]

    rows = []
    for cat in CATEGORIES:
        ps = prompts_for(cat)
        if not ps:
            ps = [cat]
        tokens = TOKENIZER(ps)
        if MODEL_DEVICE is not None:
            tokens = tokens.to(MODEL_DEVICE)
        with _TORCH.no_grad():
            feats = MODEL.encode_text(tokens)
        feats = feats / feats.norm(dim=-1, keepdim=True).clamp(min=1e-12)
        # Average prompt embeddings for this category, then re-normalize.
        v = feats.mean(dim=0, keepdim=True)
        v = v / v.norm(dim=-1, keepdim=True).clamp(min=1e-12)
        rows.append(v)

    TEXT_FEATURES = _TORCH.cat(rows, dim=0)

def _refresh_proto_features():
    global PROTO_FEATURES, PROTO_MASK
    if get_ai_provider_id() == AI_PROVIDER_CLIP_LOCAL and _AI_WORKER_PROC is not None:
        try:
            _worker_sync_if_needed(force=True)
        except Exception:
            pass
        return
    # Called during model load; don't gate on _MODEL_READY.
    if _TORCH is None or MODEL_DEVICE is None or TEXT_FEATURES is None:
        PROTO_FEATURES = None
        PROTO_MASK = None
        return
    dim = int(TEXT_FEATURES.shape[-1])

    embs = []
    mask = []
    for c in CATEGORIES:
        p = PROTOTYPES.get(c)
        if isinstance(p, dict) and isinstance(p.get("embedding"), list):
            try:
                vec = np.array([float(x) for x in p["embedding"]], dtype=np.float32)
            except Exception:
                vec = None
            if vec is not None and vec.size == dim:
                # ensure unit vector
                n = float(np.linalg.norm(vec))
                if n > 0:
                    vec = vec / n
                embs.append(vec)
                mask.append(True)
                continue
        embs.append(np.zeros((dim,), dtype=np.float32))
        mask.append(False)

    arr = np.stack(embs, axis=0)
    t = _TORCH.from_numpy(arr).to(MODEL_DEVICE)
    t = t / t.norm(dim=-1, keepdim=True).clamp(min=1e-12)
    PROTO_FEATURES = t
    PROTO_MASK = _TORCH.tensor(mask, dtype=_TORCH.bool, device=MODEL_DEVICE)

def _worker_state_token() -> str:
    payload = {
        "categories": CATEGORIES,
        "prototypes": PROTOTYPES,
        "model_profile_id": AI_MODEL_PROFILE_ID,
        "model_name": MODEL_NAME,
        "model_pretrained": MODEL_PRETRAINED,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _worker_request(payload: dict, timeout_s: float = 300.0) -> dict:
    global _AI_WORKER_PROC
    proc = _AI_WORKER_PROC
    if proc is None or proc.stdin is None or proc.stdout is None:
        raise RuntimeError("AI worker process is not running.")

    try:
        proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        proc.stdin.flush()
    except Exception as e:
        _terminate_ai_worker()
        raise RuntimeError(f"Failed writing to AI worker: {e}") from e

    deadline = time.time() + float(timeout_s)
    while True:
        if time.time() > deadline:
            _terminate_ai_worker()
            raise RuntimeError("Timed out waiting for AI worker response.")
        line = proc.stdout.readline()
        if not line:
            rc = proc.poll()
            _terminate_ai_worker()
            raise RuntimeError(f"AI worker exited unexpectedly (code {rc}).")
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except Exception:
            # Skip non-JSON worker chatter and keep reading.
            continue


def _ensure_clip_worker_running(status_cb=None) -> None:
    global _AI_WORKER_PROC, _AI_WORKER_PROVIDER_ID
    global _MODEL_READY, _MODEL_LOAD_ERROR, MODEL_DEVICE

    def say(msg: str) -> None:
        try:
            if callable(status_cb):
                status_cb(msg)
        except Exception:
            pass
        _log_line(f"[model] {msg}")

    with _AI_WORKER_LOCK:
        if _AI_WORKER_PROC is not None and _AI_WORKER_PROC.poll() is None:
            return

        ok, msg = install_ai_provider(AI_PROVIDER_CLIP_LOCAL, status_cb=status_cb)
        if not ok:
            _MODEL_READY = False
            _MODEL_LOAD_ERROR = msg
            raise RuntimeError(msg)

        runtime_python = _provider_runtime_python(AI_PROVIDER_CLIP_LOCAL)
        script_path = _write_clip_worker_script()
        if not runtime_python.exists():
            _MODEL_READY = False
            _MODEL_LOAD_ERROR = f"Runtime python not found: {runtime_python}"
            raise RuntimeError(_MODEL_LOAD_ERROR)

        say("Starting isolated AI worker...")
        proc = subprocess.Popen(
            [str(runtime_python), str(script_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        _AI_WORKER_PROC = proc
        _AI_WORKER_PROVIDER_ID = AI_PROVIDER_CLIP_LOCAL

        resp = _worker_request(
            {
                "op": "load",
                "model_name": MODEL_NAME,
                "model_pretrained": MODEL_PRETRAINED,
                "model_cache_dir": str(MODEL_CACHE_DIR),
                "categories": CATEGORIES,
                "prototypes": PROTOTYPES,
            },
            timeout_s=1800.0,
        )
        if not bool(resp.get("ok")):
            _terminate_ai_worker()
            err = str(resp.get("error") or "unknown worker load error")
            _MODEL_READY = False
            _MODEL_LOAD_ERROR = err
            raise RuntimeError(err)

        MODEL_DEVICE = str(resp.get("device") or "cpu")
        say(str(resp.get("message") or "AI worker loaded."))


def _worker_sync_if_needed(force: bool = False) -> None:
    global _AI_WORKER_STATE_TOKEN
    token = _worker_state_token()
    if not force and _AI_WORKER_STATE_TOKEN == token:
        return
    resp = _worker_request(
        {
            "op": "sync",
            "categories": CATEGORIES,
            "prototypes": PROTOTYPES,
        },
        timeout_s=300.0,
    )
    if not bool(resp.get("ok")):
        raise RuntimeError(str(resp.get("error") or "worker sync failed"))
    _AI_WORKER_STATE_TOKEN = token


def _worker_predict(image_path: str, return_details: bool = False):
    _worker_sync_if_needed(force=False)
    resp = _worker_request({"op": "predict", "image_path": str(image_path)}, timeout_s=300.0)
    if not bool(resp.get("ok")):
        raise RuntimeError(str(resp.get("error") or "worker predict failed"))
    emb = resp.get("embedding")
    emb_np = None
    if isinstance(emb, list) and emb:
        try:
            emb_np = np.array([float(x) for x in emb], dtype=np.float32)
        except Exception:
            emb_np = None
    ranked = []
    for item in (resp.get("topk") or []):
        try:
            ranked.append((str(item.get("category") or "Uncategorized"), float(item.get("score") or 0.0)))
        except Exception:
            continue
    raw_category = str(resp.get("category") or "Uncategorized")
    raw_score = float(resp.get("score") or 0.0)
    cat, score, noise_meta = _reduce_generic_prediction_noise_explain(
        raw_category,
        raw_score,
        ranked,
    )
    if return_details:
        details = {
            "backend": "worker",
            "raw_category": raw_category,
            "raw_score": raw_score,
            "topk": [{"category": c, "score": s} for (c, s) in ranked],
            "noise_adjustment": noise_meta,
        }
        return cat, score, emb_np, details
    return cat, score, emb_np


def _worker_rank(image_path: str, topk: int = 5) -> list[tuple[str, float]]:
    _worker_sync_if_needed(force=False)
    resp = _worker_request(
        {"op": "rank", "image_path": str(image_path), "topk": int(max(1, topk))},
        timeout_s=300.0,
    )
    if not bool(resp.get("ok")):
        raise RuntimeError(str(resp.get("error") or "worker rank failed"))
    out = []
    for item in (resp.get("topk") or []):
        try:
            out.append((str(item.get("category") or "Uncategorized"), float(item.get("score") or 0.0)))
        except Exception:
            continue
    return out


def _ensure_clip_model_loaded(status_cb=None):
    global _MODEL_READY, _MODEL_LOAD_ERROR
    if _MODEL_READY:
        return
    with _MODEL_LOCK:
        if _MODEL_READY:
            return
        _ensure_clip_worker_running(status_cb=status_cb)
        _worker_sync_if_needed(force=True)
        _MODEL_READY = True
        _MODEL_LOAD_ERROR = None


def _ensure_model_loaded(status_cb=None):
    global _MODEL_READY, _MODEL_LOAD_ERROR
    provider_id = get_ai_provider_id()

    if provider_id == AI_PROVIDER_NONE:
        _MODEL_READY = True
        _MODEL_LOAD_ERROR = None
        try:
            if callable(status_cb):
                status_cb("AI provider disabled (heuristics only).")
        except Exception:
            pass
        return

    if provider_id == AI_PROVIDER_CLIP_LOCAL:
        if not is_ai_provider_installed(provider_id):
            _MODEL_READY = False
            _MODEL_LOAD_ERROR = (
                f"{get_ai_provider_display_name(provider_id)} is not installed. "
                "Install it from Settings -> AI Provider."
            )
            raise RuntimeError(_MODEL_LOAD_ERROR)
        _ensure_clip_model_loaded(status_cb=status_cb)
        return

    _MODEL_READY = False
    _MODEL_LOAD_ERROR = f"Unknown AI provider: {provider_id}"
    raise RuntimeError(_MODEL_LOAD_ERROR)

def _compute_image_embedding_from_pil(pil_img):
    if not _MODEL_READY or _TORCH is None or MODEL is None or PREPROCESS is None or MODEL_DEVICE is None:
        return None
    with _INFER_LOCK:
        image_tensor = PREPROCESS(pil_img).unsqueeze(0).to(MODEL_DEVICE)
        with _TORCH.no_grad():
            img_features = MODEL.encode_image(image_tensor)
        img_features = img_features / img_features.norm(dim=-1, keepdim=True).clamp(min=1e-12)
        return img_features[0].detach().float().cpu().numpy()

def _exif_software(pil_img) -> str:
    """Best-effort read of EXIF Software tag (common place apps write their name)."""
    try:
        ex = pil_img.getexif()
        v = ex.get(305)  # EXIF "Software"
        return (str(v) if v is not None else "").strip()
    except Exception:
        return ""

# Common iPhone screenshot resolutions (portrait). Accept rotated too.
_IPHONE_SCREENSHOT_SIZES = {
    (640, 1136),   # 5/5s/SE1
    (750, 1334),   # 6/7/8/SE2/SE3
    (828, 1792),   # XR/11
    (1125, 2436),  # X/XS/11 Pro
    (1242, 2688),  # XS Max/11 Pro Max
    (1170, 2532),  # 12/13/14
    (1284, 2778),  # 12/13/14 Plus/Pro Max (also common export)
    (1179, 2556),  # 15/15 Pro
    (1290, 2796),  # 14 Pro Max / 15 Plus / 15 Pro Max (varies by model)
}

def _looks_like_iphone_screenshot(image_path: str, pil_img) -> bool:
    try:
        name = os.path.basename(image_path or "").lower()
        if "screenshot" in name or "screen shot" in name:
            return True
        ext = os.path.splitext(name)[1]
        if ext not in (".png", ".jpg", ".jpeg", ".heic", ".heif"):
            return False
        if pil_img is None:
            return False
        w, h = pil_img.size
        if (w, h) in _IPHONE_SCREENSHOT_SIZES or (h, w) in _IPHONE_SCREENSHOT_SIZES:
            return True
        return False
    except Exception:
        return False

def _looks_like_facebook_download(image_path: str, pil_img) -> bool:
    try:
        name = os.path.basename(image_path or "").lower()
        # Common filename patterns seen in Facebook app/downloads.
        if name.startswith("fb_img_") or name.startswith("fbimg_") or name.startswith("facebook_"):
            return True
        if "facebook" in name and os.path.splitext(name)[1] in IMAGE_EXT:
            return True
        # Sometimes saved into a folder path containing "Facebook".
        if "facebook" in (image_path or "").lower():
            return True
        if pil_img is not None:
            sw = _exif_software(pil_img).lower()
            if "facebook" in sw or sw.startswith("fb"):
                return True
        return False
    except Exception:
        return False

def _heuristic_category(image_path: str, pil_img):
    """Return a category override (string) if a strong heuristic matches, else None.

    Note: these are intended as *source/UI* buckets. We keep them conservative so they
    don't override strong content-based categories (e.g. "political meme").
    """
    # This function now only returns a suggested bucket; the caller decides if it should override.
    if _looks_like_facebook_download(image_path, pil_img) and "facebook download" in CATEGORIES:
        return "facebook download"
    if _looks_like_iphone_screenshot(image_path, pil_img):
        if "iphone screenshot" in CATEGORIES:
            return "iphone screenshot"
        if "screenshot" in CATEGORIES:
            return "screenshot"
    return None


def _reduce_generic_prediction_noise(
    category: str,
    score: float,
    ranked: list[tuple[str, float]] | None,
) -> tuple[str, float]:
    """Prefer specific categories when generic labels are near-ties."""
    cat, sc, _meta = _reduce_generic_prediction_noise_explain(category, score, ranked)
    return cat, sc


def _reduce_generic_prediction_noise_explain(
    category: str,
    score: float,
    ranked: list[tuple[str, float]] | None,
) -> tuple[str, float, dict]:
    """Noise-reduction with metadata that explains any category switch."""
    try:
        base_score = float(score)
    except Exception:
        base_score = 0.0

    base_cat = _canonical_category_name(category)
    if not base_cat:
        base_cat = str(category or "Uncategorized")

    cleaned = []
    seen = set()
    for raw_cat, raw_score in ranked or []:
        cat = _canonical_category_name(raw_cat)
        if not cat or cat in seen:
            continue
        try:
            sc = float(raw_score)
        except Exception:
            continue
        if not np.isfinite(sc) or sc < -1e8:
            continue
        seen.add(cat)
        cleaned.append((cat, sc))

    if base_cat not in seen:
        cleaned.insert(0, (base_cat, base_score))

    meta = {
        "applied": False,
        "rule": None,
        "from_category": base_cat,
        "to_category": base_cat,
        "from_score": base_score,
        "to_score": base_score,
        "switch_margin": None,
    }

    # 1) If the winner is generic and a specific category is very close, prefer specific.
    if base_cat in _GENERIC_CATEGORIES:
        for cat, sc in cleaned:
            if cat == base_cat:
                continue
            if cat in _GENERIC_CATEGORIES:
                continue
            if (base_score - sc) <= _GENERIC_SWITCH_MARGIN:
                meta.update(
                    {
                        "applied": True,
                        "rule": "generic_to_specific_near_tie",
                        "to_category": cat,
                        "to_score": sc,
                        "switch_margin": float(base_score - sc),
                        "threshold": _GENERIC_SWITCH_MARGIN,
                    }
                )
                return cat, sc, meta
            break

    # 2) Collapse "selfie/indoor" near-ties toward family photo for more stable buckets.
    if base_cat in _FAMILY_STABILITY_CATEGORIES:
        for cat, sc in cleaned:
            if cat != "family photo":
                continue
            if (base_score - sc) <= _FAMILY_SWITCH_MARGIN:
                meta.update(
                    {
                        "applied": True,
                        "rule": "family_stability_near_tie",
                        "to_category": "family photo",
                        "to_score": sc,
                        "switch_margin": float(base_score - sc),
                        "threshold": _FAMILY_SWITCH_MARGIN,
                    }
                )
                return "family photo", sc, meta
            break

    return base_cat, base_score, meta

def _update_prototype(category: str, embedding_np) -> None:
    global _AI_WORKER_STATE_TOKEN
    if embedding_np is None:
        return
    if not category or category not in CATEGORIES:
        return

    vec = np.asarray(embedding_np, dtype=np.float32)
    n = float(np.linalg.norm(vec))
    if n <= 0:
        return
    vec = vec / n

    existing = PROTOTYPES.get(category)
    if isinstance(existing, dict) and isinstance(existing.get("embedding"), list):
        try:
            old = np.array([float(x) for x in existing["embedding"]], dtype=np.float32)
        except Exception:
            old = None
        cnt = int(existing.get("count") or 1)
        if old is not None and old.size == vec.size and cnt > 0:
            new = (old * cnt + vec) / (cnt + 1)
            nn = float(np.linalg.norm(new))
            if nn > 0:
                new = new / nn
            PROTOTYPES[category] = {"count": cnt + 1, "embedding": new.tolist()}
        else:
            PROTOTYPES[category] = {"count": 1, "embedding": vec.tolist()}
    else:
        PROTOTYPES[category] = {"count": 1, "embedding": vec.tolist()}

    _save_prototypes()
    _AI_WORKER_STATE_TOKEN = None
    if _MODEL_READY:
        _refresh_proto_features()

def pil_to_qpixmap(img, max_size=(400,400)):
    img = img.copy()
    img.thumbnail(max_size, Image.Resampling.LANCZOS)
    img_qt = QPixmap.fromImage(
        ImageQt(img)
    )
    return img_qt

def _predict_category_from_pil(pil_img, image_path: str | None = None, return_details: bool = False):
    """Return (category, score, embedding_np) for a pre-loaded PIL image (no CORRECTIONS lookup)."""
    try:
        provider_id = get_ai_provider_id()
        if provider_id != AI_PROVIDER_CLIP_LOCAL:
            if return_details:
                return "Uncategorized", 0.0, None, {"reason": "provider_not_clip_local", "topk": []}
            return "Uncategorized", 0.0, None
        if image_path:
            if return_details:
                return _worker_predict(str(image_path), return_details=True)
            return _worker_predict(str(image_path))
        if not CATEGORIES or TEXT_FEATURES is None or MODEL is None or PREPROCESS is None or _TORCH is None or MODEL_DEVICE is None:
            if return_details:
                return "Uncategorized", 0.0, None, {"reason": "model_or_categories_unavailable", "topk": []}
            return "Uncategorized", 0.0, None

        with _INFER_LOCK:
            image_tensor = PREPROCESS(pil_img).unsqueeze(0).to(MODEL_DEVICE)
            with _TORCH.no_grad():
                img_features = MODEL.encode_image(image_tensor)
            img_features = img_features / img_features.norm(dim=-1, keepdim=True).clamp(min=1e-12)

            sims_text = (img_features @ TEXT_FEATURES.T)[0]
            combined = sims_text

            if PROTO_FEATURES is not None and PROTO_MASK is not None and bool(PROTO_MASK.any().item()):
                sims_proto = (img_features @ PROTO_FEATURES.T)[0]
                proto_w = 0.70
                combined = sims_text.clone()
                combined[PROTO_MASK] = (1.0 - proto_w) * sims_text[PROTO_MASK] + proto_w * sims_proto[PROTO_MASK]

            # Guardrails: prevent "source" categories from winning without supporting evidence.
            # This avoids e.g. camera photos being labeled "iphone screenshot" just because the text prompt is too attractive.
            try:
                if image_path:
                    if "iphone screenshot" in CATEGORIES and not _looks_like_iphone_screenshot(image_path, pil_img):
                        idx = CATEGORIES.index("iphone screenshot")
                        combined[idx] = -1e9
                    if "facebook download" in CATEGORIES and not _looks_like_facebook_download(image_path, pil_img):
                        idx = CATEGORIES.index("facebook download")
                        combined[idx] = -1e9
            except Exception:
                pass

            best_idx = int(combined.argmax().item())
            best_score = float(combined[best_idx].item())
            emb_np = img_features[0].detach().float().cpu().numpy()
            ranked = []
            try:
                k = min(3, int(combined.numel()))
                vals, idxs = _TORCH.topk(combined, k=k, largest=True, sorted=True)
                for v, i in zip(vals.tolist(), idxs.tolist()):
                    ranked.append((CATEGORIES[int(i)], float(v)))
            except Exception:
                ranked = []

        raw_category = CATEGORIES[best_idx]
        raw_score = best_score
        cat, score, noise_meta = _reduce_generic_prediction_noise_explain(raw_category, raw_score, ranked)
        if return_details:
            details = {
                "backend": "in_process",
                "raw_category": raw_category,
                "raw_score": raw_score,
                "topk": [{"category": c, "score": s} for (c, s) in ranked],
                "noise_adjustment": noise_meta,
            }
            return cat, score, emb_np, details
        return cat, score, emb_np
    except Exception as e:
        if return_details:
            return "Uncategorized", 0.0, None, {"reason": f"predict_exception: {e}", "topk": []}
        return "Uncategorized", 0.0, None

def _rank_categories_from_pil(pil_img, topk: int = 5, image_path: str | None = None):
    """Return [(category, score), ...] highest-first for a pre-loaded PIL image."""
    if topk <= 0:
        return []
    try:
        provider_id = get_ai_provider_id()
        if provider_id != AI_PROVIDER_CLIP_LOCAL:
            return []
        if image_path:
            return _worker_rank(str(image_path), topk=int(topk))
        if not CATEGORIES or TEXT_FEATURES is None or MODEL is None or PREPROCESS is None or _TORCH is None or MODEL_DEVICE is None:
            return []

        with _INFER_LOCK:
            image_tensor = PREPROCESS(pil_img).unsqueeze(0).to(MODEL_DEVICE)
            with _TORCH.no_grad():
                img_features = MODEL.encode_image(image_tensor)
            img_features = img_features / img_features.norm(dim=-1, keepdim=True).clamp(min=1e-12)

            sims_text = (img_features @ TEXT_FEATURES.T)[0]
            combined = sims_text

            if PROTO_FEATURES is not None and PROTO_MASK is not None and bool(PROTO_MASK.any().item()):
                sims_proto = (img_features @ PROTO_FEATURES.T)[0]
                proto_w = 0.70
                combined = sims_text.clone()
                combined[PROTO_MASK] = (1.0 - proto_w) * sims_text[PROTO_MASK] + proto_w * sims_proto[PROTO_MASK]

            try:
                if image_path:
                    if "iphone screenshot" in CATEGORIES and not _looks_like_iphone_screenshot(image_path, pil_img):
                        idx = CATEGORIES.index("iphone screenshot")
                        combined[idx] = -1e9
                    if "facebook download" in CATEGORIES and not _looks_like_facebook_download(image_path, pil_img):
                        idx = CATEGORIES.index("facebook download")
                        combined[idx] = -1e9
            except Exception:
                pass

            k = min(int(topk), int(combined.numel()))
            vals, idxs = _TORCH.topk(combined, k=k, largest=True, sorted=True)

        out = []
        for v, i in zip(vals.tolist(), idxs.tolist()):
            try:
                out.append((CATEGORIES[int(i)], float(v)))
            except Exception:
                continue
        return out
    except Exception:
        return []

def _predict_category_internal(image_path, pil_img=None):
    """Return (category, score, embedding_np). score is cosine similarity after combining signals."""
    global _HEIC_LOAD_WARNING_SHOWN
    provider_id = get_ai_provider_id()
    decision = {
        "event": "classification",
        "file_path": str(image_path or ""),
        "file_name": os.path.basename(str(image_path or "")),
        "provider_id": str(provider_id),
        "provider_label": get_ai_provider_display_name(provider_id),
        "model_profile_id": get_ai_model_id(),
        "model_name": str(MODEL_NAME),
        "model_pretrained": str(MODEL_PRETRAINED),
    }
    img_hash = hash_image(image_path)
    if img_hash:
        decision["image_hash"] = img_hash
    if img_hash in CORRECTIONS:
        chosen = CORRECTIONS[img_hash]
        if isinstance(chosen, str) and chosen.strip():
            decision.update(
                {
                    "reason": "user_correction",
                    "final_category": str(chosen),
                    "final_score": 1.0,
                }
            )
            _finalize_classification_decision(decision)
            return chosen, 1.0, None

    if pil_img is None:
        pil_img = load_image_for_ai(image_path)
    if pil_img is None:
        decision["reason"] = "image_load_failed"
        lower_path = (image_path or "").lower()
        if lower_path.endswith((".heic", ".heif")):
            heic = get_heic_support_status()
            decision["heic_support"] = heic
            if not bool(heic.get("supported")) and not _HEIC_LOAD_WARNING_SHOWN:
                _HEIC_LOAD_WARNING_SHOWN = True
                print(f"Warning: HEIC support unavailable. {heic.get('detail')}")
                _log_line(f"[heic][warn] {heic.get('detail')}")
                if _PILLOW_HEIF_IMPORT_TRACE:
                    try:
                        for line in _PILLOW_HEIF_IMPORT_TRACE.splitlines():
                            if line.strip():
                                _log_line(f"[heic][warn-trace] {line.rstrip()}")
                    except Exception:
                        pass
        print(f"Warning: Failed to load {image_path}")
        decision.update({"final_category": "Uncategorized", "final_score": 0.0})
        _finalize_classification_decision(decision)
        return "Uncategorized", 0.0, None

    if provider_id == AI_PROVIDER_NONE:
        override = _heuristic_category(image_path, pil_img)
        decision["heuristic_suggestion"] = override
        if override:
            decision.update(
                {
                    "reason": "heuristics_only_provider",
                    "final_category": str(override),
                    "final_score": 1.0,
                }
            )
            _finalize_classification_decision(decision)
            return override, 1.0, None
        decision.update(
            {
                "reason": "heuristics_only_provider_no_match",
                "final_category": "Uncategorized",
                "final_score": 0.0,
            }
        )
        _finalize_classification_decision(decision)
        return "Uncategorized", 0.0, None

    if not _MODEL_READY:
        decision.update(
            {
                "reason": "model_not_ready",
                "model_load_error": str(_MODEL_LOAD_ERROR or ""),
                "final_category": "Uncategorized",
                "final_score": 0.0,
            }
        )
        _finalize_classification_decision(decision)
        return "Uncategorized", 0.0, None

    cat, score, emb, model_details = _predict_category_from_pil(
        pil_img,
        image_path=image_path,
        return_details=True,
    )
    decision["model"] = model_details
    decision["model_prediction"] = {"category": str(cat), "score": float(score)}

    # If the model says "pet" but the image looks like a close-up of a person, prefer "family photo".
    # This fixes a common CLIP failure mode on close-up portraits.
    if cat == "pet" and "family photo" in CATEGORIES:
        face_frac = _largest_face_fraction(pil_img)
        decision["face_fraction"] = float(face_frac)
        if face_frac >= 0.05:
            emb = emb if emb is not None else _compute_image_embedding_from_pil(pil_img)
            decision.update(
                {
                    "reason": "face_override_pet_to_family_photo",
                    "final_category": "family photo",
                    "final_score": 1.0,
                }
            )
            _finalize_classification_decision(decision)
            return "family photo", 1.0, emb

    # Conservative source/screenshot bucketing: only override when the model already thinks
    # it's in the "screenshot/document-ish" cluster. This keeps content categories (like
    # "political meme") from being stomped just because the file is a screenshot/download.
    override = _heuristic_category(image_path, pil_img)
    decision["heuristic_suggestion"] = override
    if override == "facebook download":
        if cat in ("screenshot", "document", "iphone screenshot", "facebook download"):
            emb = emb if emb is not None else _compute_image_embedding_from_pil(pil_img)
            decision.update(
                {
                    "reason": "heuristic_override_facebook_download",
                    "heuristic_eligible_from": str(cat),
                    "final_category": "facebook download",
                    "final_score": 1.0,
                }
            )
            _finalize_classification_decision(decision)
            return "facebook download", 1.0, emb
    elif override in ("iphone screenshot", "screenshot"):
        if cat in ("screenshot", "document", "iphone screenshot"):
            want = "iphone screenshot" if "iphone screenshot" in CATEGORIES else "screenshot"
            emb = emb if emb is not None else _compute_image_embedding_from_pil(pil_img)
            decision.update(
                {
                    "reason": "heuristic_override_screenshot_family",
                    "heuristic_eligible_from": str(cat),
                    "final_category": str(want),
                    "final_score": 1.0,
                }
            )
            _finalize_classification_decision(decision)
            return want, 1.0, emb

    decision.update(
        {
            "reason": "model_prediction",
            "final_category": str(cat),
            "final_score": float(score),
        }
    )
    _finalize_classification_decision(decision)
    return cat, score, emb

def predict_category(image_path):
    cat, _, _ = _predict_category_internal(image_path)
    return cat

# ---------------------------
# THREAD FOR MODEL LOADING
# ---------------------------

class ModelLoadThread(QThread):
    status_signal = Signal(str)
    done_signal = Signal(bool, str)

    def run(self):
        try:
            _ensure_model_loaded(status_cb=self.status_signal.emit)
            if get_ai_provider_id() == AI_PROVIDER_NONE:
                self.done_signal.emit(True, f"{get_ai_provider_display_name()} ready")
            else:
                self.done_signal.emit(
                    True, f"{get_ai_provider_display_name()} ready ({MODEL_NAME} on {MODEL_DEVICE})"
                )
        except Exception as e:
            self.done_signal.emit(False, _MODEL_LOAD_ERROR or str(e))


class ProviderInstallThread(QThread):
    status_signal = Signal(str)
    done_signal = Signal(bool, str)

    def __init__(self, provider_id: str):
        super().__init__()
        self.provider_id = (provider_id or "").strip()

    def run(self):
        try:
            ok, message = install_ai_provider(
                provider_id=self.provider_id, status_cb=self.status_signal.emit
            )
            self.done_signal.emit(bool(ok), str(message))
        except Exception as e:
            self.done_signal.emit(False, str(e))

# ---------------------------
# THREAD FOR AUTO-PROCESSING
# ---------------------------

class AutoProcessThread(QThread):
    progress_signal = Signal(int)
    status_signal = Signal(str)
    done_signal = Signal(dict)
    visual_signal = Signal(dict)  # live review payload for the current file

    def __init__(
        self,
        files,
        input_folder,
        output_folder,
        convert_videos=True,
        start_index=0,
        structure_pattern="{category}",
        enable_people=False,
    ):
        super().__init__()
        self.files = files
        self.input_folder = input_folder
        self.output_folder = output_folder
        self.convert_videos = convert_videos
        self.start_index = start_index
        self.structure_pattern = (structure_pattern or "{category}").strip() or "{category}"
        self.enable_people = bool(enable_people)

        # People clustering (post-run labeling).
        self.people_clusters = []  # list of dicts
        self.people_output_map = {}  # image_path -> canonical output image path

        # Seed with known people prototypes.
        try:
            self.people_db = _load_people_db()
        except Exception:
            self.people_db = {}

        self._people_known = []  # list of (name, emb_np unit, count)
        for name, v in (self.people_db or {}).items():
            try:
                vec = np.array([float(x) for x in (v.get("embedding") or [])], dtype=np.float32)
                n = float(np.linalg.norm(vec))
                if n > 0:
                    vec = vec / n
                cnt = int(v.get("count") or 1)
                self._people_known.append((name, vec, cnt))
            except Exception:
                pass

        self._face_match_threshold = 0.45  # cosine similarity threshold; tune over time

        # ETA bookkeeping (best-effort).
        self._alpha = 0.25  # EMA smoothing; higher = more responsive.
        self._ema_img_s = None          # seconds per image
        self._ema_vid_copy_s_per_mb = None   # seconds per MB copied
        self._ema_vid_conv_s_per_mb = None   # seconds per MB converted (input MB)
        self._processed = 0
        self._t_start = time.time()

        self._rem_img = 0
        self._rem_vid = 0
        self._rem_img_mb = 0.0
        self._rem_vid_mb = 0.0

        # Pre-scan remaining slice so ETA can use remaining bytes/counts.
        try:
            for f in self.files[self.start_index:]:
                p = os.path.join(self.input_folder, f)
                try:
                    mb = float(os.path.getsize(p)) / (1024.0 * 1024.0)
                except Exception:
                    mb = 0.0
                if f.lower().endswith(IMAGE_EXT):
                    self._rem_img += 1
                    self._rem_img_mb += mb
                elif f.lower().endswith(VIDEO_EXT):
                    self._rem_vid += 1
                    self._rem_vid_mb += mb
        except Exception:
            pass

    def _ema(self, prev, value: float):
        try:
            v = float(value)
        except Exception:
            return prev
        if v <= 0:
            return prev
        if prev is None:
            return v
        return (1.0 - self._alpha) * float(prev) + self._alpha * v

    def _estimate_remaining_seconds(self) -> float | None:
        # Prefer type-aware estimates; fall back to overall rate after a few items.
        try:
            rem = 0.0
            have_any = False

            if self._rem_img > 0 and self._ema_img_s is not None:
                rem += float(self._rem_img) * float(self._ema_img_s)
                have_any = True

            if self._rem_vid > 0:
                if self.convert_videos and self._ema_vid_conv_s_per_mb is not None:
                    rem += float(self._rem_vid_mb) * float(self._ema_vid_conv_s_per_mb)
                    have_any = True
                elif (not self.convert_videos) and self._ema_vid_copy_s_per_mb is not None:
                    rem += float(self._rem_vid_mb) * float(self._ema_vid_copy_s_per_mb)
                    have_any = True

            if have_any:
                return max(0.0, rem)

            if self._processed >= 3:
                elapsed = time.time() - self._t_start
                per_item = elapsed / float(self._processed)
                return max(0.0, float(self._rem_img + self._rem_vid) * per_item)

            return None
        except Exception:
            return None

    def _people_add_face(self, image_path: str, emb: np.ndarray, bbox) -> None:
        """Assign face embedding to a known person or an unknown cluster."""
        try:
            v = np.asarray(emb, dtype=np.float32).flatten()
            n = float(np.linalg.norm(v))
            if n > 0:
                v = v / n
        except Exception:
            return

        # 1) Match known people first.
        best_name = None
        best_sim = -1.0
        for name, vec, _cnt in self._people_known:
            sim = float(np.dot(v, vec))
            if sim > best_sim:
                best_sim = sim
                best_name = name
        if best_name is not None and best_sim >= self._face_match_threshold:
            # Add to a named cluster bucket (one per name).
            for cl in self.people_clusters:
                if cl.get("name") == best_name:
                    cl["files"].add(image_path)
                    cl["count"] = int(cl.get("count") or 0) + 1
                    return
            self.people_clusters.append(
                {"name": best_name, "files": {image_path}, "count": 1, "rep_path": image_path, "rep_bbox": bbox, "centroid": v}
            )
            return

        # 2) Otherwise cluster as unknown.
        best_idx = -1
        best_sim = -1.0
        for i, cl in enumerate(self.people_clusters):
            if cl.get("name"):
                continue
            c = cl.get("centroid")
            if c is None:
                continue
            sim = float(np.dot(v, c))
            if sim > best_sim:
                best_sim = sim
                best_idx = i

        if best_idx >= 0 and best_sim >= self._face_match_threshold:
            cl = self.people_clusters[best_idx]
            cl["files"].add(image_path)
            cl["count"] = int(cl.get("count") or 0) + 1
            # Update centroid (running average)
            try:
                cnt = int(cl.get("count") or 1)
                c = np.asarray(cl.get("centroid"), dtype=np.float32)
                new = (c * (cnt - 1) + v) / float(cnt)
                nn = float(np.linalg.norm(new))
                if nn > 0:
                    new = new / nn
                cl["centroid"] = new
            except Exception:
                pass
            return

        # New unknown cluster
        self.people_clusters.append(
            {"name": None, "files": {image_path}, "count": 1, "rep_path": image_path, "rep_bbox": bbox, "centroid": v}
        )

    def _iter_output_images_recursive(self):
        try:
            for root, _dirs, files in os.walk(self.output_folder):
                if self.isInterruptionRequested():
                    return
                for name in files:
                    if str(name).lower().endswith(IMAGE_EXT):
                        yield os.path.join(root, name)
        except Exception:
            return

    def _scan_people_from_output_recursive(self) -> None:
        if not self.enable_people:
            return

        paths = list(self._iter_output_images_recursive() or [])
        total = len(paths)
        if total <= 0:
            self.status_signal.emit("People scan: no images found in output.")
            return

        self.status_signal.emit(f"People scan: scanning output recursively ({total} images)...")
        scanned = 0
        with_faces = 0

        for path in paths:
            if self.isInterruptionRequested():
                break
            scanned += 1
            if scanned == 1 or scanned == total or (scanned % 50) == 0:
                self.status_signal.emit(f"People scan {scanned}/{total}: {os.path.basename(path)}")
            try:
                img = load_image_for_ai(path)
                if img is None:
                    continue
                faces = _extract_face_embeddings(path, pil_img=img, max_faces=1)
                if not faces:
                    continue
                with_faces += 1
                self.people_output_map[path] = path
                for face in faces:
                    emb = face.get("embedding")
                    bbox = face.get("bbox")
                    if emb is None or bbox is None:
                        continue
                    self._people_add_face(path, emb, bbox)
            except Exception:
                continue

        self.status_signal.emit(
            f"People scan complete: {with_faces} images with faces, {len(self.people_clusters)} clusters."
        )

    def run(self):
        counts = {"images":0, "videos":0, "failed":0}
        for i in range(self.start_index, len(self.files)):
            if self.isInterruptionRequested():
                break
            file = self.files[i]
            path = os.path.join(self.input_folder, file)
            self.progress_signal.emit(i+1)

            # Provide a stable "where am I" + ETA message during processing.
            total = max(1, len(self.files))
            eta_s = self._estimate_remaining_seconds()
            if eta_s is None:
                eta_part = " ETA: estimating..."
            else:
                eta_part = f" ETA {_fmt_duration(eta_s)}"
            self.status_signal.emit(f"Processing {file} ({i+1}/{total}).{eta_part}")

            is_vid = file.lower().endswith(VIDEO_EXT)
            is_img = file.lower().endswith(IMAGE_EXT)
            try:
                mb = float(os.path.getsize(path)) / (1024.0 * 1024.0)
            except Exception:
                mb = 0.0

            t0 = time.time()
            try:
                if is_vid:
                    out_dir = os.path.join(self.output_folder, "Videos")
                    os.makedirs(out_dir, exist_ok=True)
                    if self.convert_videos:
                        base = os.path.splitext(file)[0]
                        mp4_path = _unique_dest_path(out_dir, base + ".mp4")
                        convert_video(path, mp4_path)
                        _log_sort_destination_decision(
                            source_path=path,
                            category="Videos",
                            structure_pattern="Videos",
                            tokens={"category": "Videos"},
                            dest_dir=out_dir,
                            dest_path=mp4_path,
                            flow="auto_video_convert",
                        )
                    else:
                        # Copy video as-is (fast path). Ensure we don't overwrite existing files.
                        dest = _unique_dest_path(out_dir, file)
                        shutil.copy2(path, dest)
                        _log_sort_destination_decision(
                            source_path=path,
                            category="Videos",
                            structure_pattern="Videos",
                            tokens={"category": "Videos"},
                            dest_dir=out_dir,
                            dest_path=dest,
                            flow="auto_video_copy",
                        )
                    counts["videos"] +=1
                    try:
                        self.visual_signal.emit(
                            {
                                "source_path": path,
                                "dest_path": str(mp4_path if self.convert_videos else dest),
                                "category": "Videos",
                                "is_video": True,
                                "explanation": "This file was detected as a video and placed in the Videos destination.",
                                "explanation_source": "rule_based_video",
                            }
                        )
                    except Exception:
                        pass

                    dt = max(1e-6, time.time() - t0)
                    if self.convert_videos:
                        if mb > 0:
                            self._ema_vid_conv_s_per_mb = self._ema(self._ema_vid_conv_s_per_mb, dt / mb)
                    else:
                        if mb > 0:
                            self._ema_vid_copy_s_per_mb = self._ema(self._ema_vid_copy_s_per_mb, dt / mb)
                elif is_img:
                    img = load_image_for_ai(path)
                    predicted, _, _ = _predict_category_internal(path, pil_img=img)
                    toks = _structure_tokens(predicted, path, img)
                    dest_dir = _render_structure(self.output_folder, self.structure_pattern, toks)
                    dest = _unique_dest_path(dest_dir, file)
                    shutil.copy2(path, dest)
                    _log_sort_destination_decision(
                        source_path=path,
                        category=predicted,
                        structure_pattern=self.structure_pattern,
                        tokens=toks,
                        dest_dir=dest_dir,
                        dest_path=dest,
                        flow="auto_image",
                    )
                    counts["images"] +=1
                    try:
                        ctx = _CLASSIFICATION_CONTEXT_BY_PATH.get(str(path or "")) or {}
                        self.visual_signal.emit(
                            {
                                "source_path": path,
                                "dest_path": str(dest),
                                "category": str(predicted),
                                "is_video": False,
                                "explanation_source": str(ctx.get("explanation_source") or "unknown"),
                                "explanation": str(
                                    ctx.get("explanation")
                                    or f"The image was sorted into '{_pretty_category_name(predicted)}' based on the current classification result."
                                ),
                            }
                        )
                    except Exception:
                        pass

                    dt = max(1e-6, time.time() - t0)
                    self._ema_img_s = self._ema(self._ema_img_s, dt)
            except Exception as e:
                print(f"Failed: {file}, {e}")
                counts["failed"] +=1
            finally:
                self._processed += 1
                if is_vid:
                    self._rem_vid = max(0, self._rem_vid - 1)
                    self._rem_vid_mb = max(0.0, self._rem_vid_mb - mb)
                elif is_img:
                    self._rem_img = max(0, self._rem_img - 1)
                    self._rem_img_mb = max(0.0, self._rem_img_mb - mb)

        if self.enable_people and not self.isInterruptionRequested():
            try:
                self._scan_people_from_output_recursive()
            except Exception:
                pass
        self.done_signal.emit(counts)

# ---------------------------
# MAIN GUI
# ---------------------------

from PIL.ImageQt import ImageQt

