import json
import os
import shutil
from pathlib import Path

import pytest

import mediasorter_core as core
from mediasorter_cli import main as cli_main


PHOTO_ROOT = Path(os.environ.get("MEDIASORTER_FACE_PHOTO_ROOT", r"H:\media\categories\family photo"))
SAMPLE_LIMIT = max(1, int(os.environ.get("MEDIASORTER_FACE_SAMPLE_LIMIT", "12") or 12))
EXTENSIONS = ("*.jpg", "*.jpeg", "*.png", "*.heic", "*.webp")


def _library_image_paths(limit: int) -> list[Path]:
    paths: list[Path] = []
    for pattern in EXTENSIONS:
        for path in PHOTO_ROOT.rglob(pattern):
            if path.is_file():
                paths.append(path)
                if len(paths) >= limit:
                    return paths
    return paths


@pytest.fixture(scope="module")
def sample_paths() -> list[Path]:
    if not PHOTO_ROOT.exists():
        pytest.skip(f"Photo library root not found: {PHOTO_ROOT}")
    paths = _library_image_paths(SAMPLE_LIMIT)
    if not paths:
        pytest.skip(f"No supported images found under {PHOTO_ROOT}")
    return paths


@pytest.mark.face
@pytest.mark.photo_library
def test_photo_library_root_contains_supported_images(sample_paths: list[Path]) -> None:
    assert len(sample_paths) >= 1


@pytest.mark.face
@pytest.mark.photo_library
def test_sample_images_load_for_ai(sample_paths: list[Path]) -> None:
    failures: list[str] = []
    for path in sample_paths:
        img = core.load_image_for_ai(str(path))
        if img is None:
            failures.append(str(path))
    assert not failures, f"Failed to load {len(failures)} sample images: {failures[:5]}"


@pytest.mark.face
@pytest.mark.photo_library
@pytest.mark.slow
def test_sample_images_face_extraction_smoke(sample_paths: list[Path], record_property) -> None:
    status = core.get_face_support_status()
    if not bool(status.get("supported")):
        pytest.skip(str(status.get("detail") or "Face support unavailable"))

    scanned = 0
    with_faces = 0
    per_file: list[dict[str, object]] = []

    for path in sample_paths:
        img = core.load_image_for_ai(str(path))
        assert img is not None, f"Failed to load sample image: {path}"
        faces = core._extract_face_embeddings(str(path), pil_img=img, max_faces=3)
        scanned += 1
        if faces:
            with_faces += 1
        per_file.append({"path": str(path), "faces": len(faces)})

    record_property("photo_root", str(PHOTO_ROOT))
    record_property("scanned", scanned)
    record_property("with_faces", with_faces)
    record_property("results_json", json.dumps(per_file[:20]))

    assert scanned == len(sample_paths)


@pytest.mark.face
@pytest.mark.photo_library
@pytest.mark.slow
def test_family_photo_sample_contains_detected_faces(sample_paths: list[Path]) -> None:
    status = core.get_face_support_status()
    if not bool(status.get("supported")):
        pytest.skip(str(status.get("detail") or "Face support unavailable"))

    detected = 0
    checked = 0
    for path in sample_paths:
        img = core.load_image_for_ai(str(path))
        assert img is not None, f"Failed to load sample image: {path}"
        faces = core._extract_face_embeddings(str(path), pil_img=img, max_faces=3)
        checked += 1
        if faces:
            detected += 1

    assert detected >= 1, f"Expected at least one detected face in {checked} family-photo samples from {PHOTO_ROOT}"


@pytest.mark.face
@pytest.mark.photo_library
def test_people_scan_cli_smoke(sample_paths: list[Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    status = core.get_face_support_status()
    if not bool(status.get("supported")):
        pytest.skip(str(status.get("detail") or "Face support unavailable"))

    for path in sample_paths[: min(6, len(sample_paths))]:
        shutil.copy2(path, tmp_path / path.name)

    exit_code = cli_main(
        [
            "--people-scan-dir",
            str(tmp_path),
            "--people-min-cluster",
            "1",
            "--json",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    out_lines = [line.strip() for line in (captured.out or "").splitlines() if line.strip()]
    assert out_lines, "Expected CLI output from people scan"
    payload = json.loads(out_lines[-1])
    assert payload["output_dir"] == str(tmp_path)
    assert "total_clusters" in payload
    assert "unknown_clusters" in payload
