import json
from pathlib import Path

import numpy as np
import pytest

import mediasorter_core as core


SAMPLE_ROOT = Path(__file__).resolve().parents[1] / "face_samples" / "bryan"
MANIFEST_PATH = SAMPLE_ROOT / "manifest.json"


def _load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        pytest.skip(f"Missing face-sample manifest: {MANIFEST_PATH}")
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _extract_best_face_similarity(image_path: Path, reference_vectors: list[np.ndarray]) -> tuple[int, float]:
    img = core.load_image_for_ai(str(image_path))
    assert img is not None, f"Failed to load {image_path}"
    faces = core._extract_face_embeddings(str(image_path), pil_img=img, max_faces=8)
    best = -1.0
    for face in faces:
        emb = np.asarray(face["embedding"], dtype=np.float32)
        n = float(np.linalg.norm(emb))
        if n <= 0:
            continue
        emb = emb / n
        for ref in reference_vectors:
            sim = float(np.dot(emb, ref))
            if sim > best:
                best = sim
    return len(faces), best


@pytest.mark.face
def test_bryan_face_sample_manifest_exists() -> None:
    manifest = _load_manifest()
    assert manifest["resume_sources"]
    assert manifest["family_sources"]
    assert manifest["crops"]


@pytest.mark.face
def test_resume_headshots_detect_faces() -> None:
    status = core.get_face_support_status()
    if not bool(status.get("supported")):
        pytest.skip(str(status.get("detail") or "Face support unavailable"))

    manifest = _load_manifest()
    for entry in manifest["resume_sources"]:
        image_path = Path(entry["copied_path"])
        img = core.load_image_for_ai(str(image_path))
        assert img is not None, f"Failed to load {image_path}"
        faces = core._extract_face_embeddings(str(image_path), pil_img=img, max_faces=8)
        assert len(faces) >= 1, f"Expected at least one face in {image_path}"


@pytest.mark.face
def test_family_sample_matches_resume_embedding_set() -> None:
    status = core.get_face_support_status()
    if not bool(status.get("supported")):
        pytest.skip(str(status.get("detail") or "Face support unavailable"))

    manifest = _load_manifest()
    reference_vectors: list[np.ndarray] = []
    for entry in manifest["resume_sources"]:
        image_path = Path(entry["copied_path"])
        img = core.load_image_for_ai(str(image_path))
        assert img is not None, f"Failed to load {image_path}"
        faces = core._extract_face_embeddings(str(image_path), pil_img=img, max_faces=8)
        for face in faces:
            emb = np.asarray(face["embedding"], dtype=np.float32)
            n = float(np.linalg.norm(emb))
            if n > 0:
                reference_vectors.append(emb / n)

    assert reference_vectors, "Expected at least one reference face embedding from resume headshots"

    family_path = Path(manifest["family_sources"][0]["copied_path"])
    face_count, best_similarity = _extract_best_face_similarity(family_path, reference_vectors)
    assert face_count >= 1, f"Expected at least one face in family sample {family_path}"
    assert best_similarity >= 0.9, f"Expected strong match for {family_path}, got similarity {best_similarity:.3f}"
