import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

import mediasorter_core as core
from mediasorter_window import MediaSorter


def main(argv=None):
    provider_options = core.get_ai_provider_options()
    provider_ids = [str(opt.get("id")) for opt in provider_options if opt.get("id")]
    model_options = core.get_ai_model_options(provider_id=core.AI_PROVIDER_CLIP_LOCAL)
    model_ids = [str(opt.get("id")) for opt in model_options if opt.get("id")]
    parser = argparse.ArgumentParser(description="MediaSorter - runtime-selectable AI media sorter")
    parser.add_argument(
        "--ai-provider",
        choices=provider_ids,
        help="Select AI provider (none, clip_local). Saved to local app settings.",
    )
    parser.add_argument(
        "--ai-model",
        choices=model_ids,
        help="Select local CLIP model profile (saved to local app settings).",
    )
    parser.add_argument("--list-ai-providers", action="store_true", help="List available AI providers and exit.")
    parser.add_argument("--list-ai-models", action="store_true", help="List available local AI model profiles and exit.")
    parser.add_argument(
        "--install-ai-provider",
        action="store_true",
        help="Install packages for the selected AI provider and exit.",
    )
    parser.add_argument(
        "--download-model",
        action="store_true",
        help="Initialize/download model assets for the selected AI provider and exit.",
    )
    parser.add_argument(
        "--heic-status",
        action="store_true",
        help="Print HEIC/HEIF decoder status for the current runtime and exit.",
    )
    parser.add_argument("--classify-dir", help="Classify images in a folder and print results (no GUI).")
    parser.add_argument("--topk", type=int, default=3, help="With --classify-dir, show the top K matches.")
    parser.add_argument("--json", action="store_true", help="With --classify-dir, output JSON lines.")
    parser.add_argument("--sort", action="store_true", help="Copy media into categorized folders (no GUI). Requires --input and --output.")
    parser.add_argument("--dry-run", action="store_true", help="With --sort, only print what would be copied.")
    parser.add_argument("--timeline", action="store_true", help="With --sort, create year folders from photo date (EXIF, else mtime).")
    parser.add_argument("--month", action="store_true", help="With --sort --timeline, include month folders under year.")
    parser.add_argument("--location", action="store_true", help="With --sort, create GPS-based location folders when available.")
    parser.add_argument("--structure", help="With --sort, folder structure pattern, e.g. {category}/{yearmo}/{location}")
    parser.add_argument("--input", help="Autorun: input folder")
    parser.add_argument("--output", help="Autorun: output folder")
    parser.add_argument("--interactive", action="store_true", help="Autorun: interactive mode")
    parser.add_argument("--convert-videos", action="store_true", help="Autorun: convert videos to MP4 via HandBrake")
    args = parser.parse_args(argv)

    if args.list_ai_providers:
        current = core.get_ai_provider_id()
        for opt in provider_options:
            pid = str(opt.get("id"))
            label = str(opt.get("label") or pid)
            mark = "*" if pid == current else " "
            print(f"{mark} {pid}: {label}")
        return 0

    if args.list_ai_models:
        current = core.get_ai_model_id()
        for opt in model_options:
            mid = str(opt.get("id"))
            label = str(opt.get("label") or mid)
            mark = "*" if mid == current else " "
            print(f"{mark} {mid}: {label}")
        return 0

    if args.ai_provider:
        core.set_ai_provider(args.ai_provider)
    if args.ai_model:
        core.set_ai_model_profile(args.ai_model)

    if args.install_ai_provider:
        ok, message = core.install_ai_provider(provider_id=core.get_ai_provider_id(), status_cb=print)
        print(message)
        return 0 if ok else 1

    if args.download_model:
        core._ensure_model_loaded(status_cb=print)
        print("OK")
        return 0

    if args.heic_status:
        status = core.get_heic_support_status()
        print(json.dumps(status, ensure_ascii=False))
        return 0

    if args.classify_dir:
        in_dir = Path(args.classify_dir).expanduser().resolve()
        if not in_dir.exists() or not in_dir.is_dir():
            print(f"Not a directory: {in_dir}")
            return 2

        core._ensure_model_loaded(status_cb=print)

        files = sorted([p for p in in_dir.iterdir() if p.is_file() and p.name.lower().endswith(core.IMAGE_EXT)])
        if not files:
            print(f"No supported images found in: {in_dir}")
            return 0

        for p in files:
            img = core.load_image_for_ai(str(p))
            if img is None:
                rec = {"file": p.name, "path": str(p), "error": "failed_to_load"}
                if args.json:
                    print(json.dumps(rec, ensure_ascii=False))
                else:
                    print(f"{p.name} -> (failed to load)")
                continue

            ranked = core._rank_categories_from_pil(img, topk=int(args.topk or 3), image_path=str(p))
            if not ranked:
                fallback_cat, fallback_score, _ = core._predict_category_internal(str(p), pil_img=img)
                if fallback_cat and fallback_cat != "Uncategorized":
                    ranked = [(fallback_cat, float(fallback_score))]
            best_cat, best_score = (ranked[0] if ranked else ("Uncategorized", 0.0))

            if args.json:
                rec = {
                    "file": p.name,
                    "path": str(p),
                    "best": {"category": best_cat, "score": best_score},
                    "topk": [{"category": c, "score": s} for (c, s) in ranked],
                }
                print(json.dumps(rec, ensure_ascii=False))
            else:
                tail = " | ".join([f"{c} ({s:.2f})" for (c, s) in ranked])
                print(f"{p.name} -> {tail}")

        return 0

    if args.sort:
        if args.month and not args.timeline:
            args.timeline = True

        pattern = (args.structure or "").strip()
        if not pattern:
            parts = []
            if args.timeline:
                parts.append("{year}")
                if args.month:
                    parts.append("{month}")
            if args.location:
                parts.append("{location}")
            parts.append("{category}")
            pattern = "/".join(parts) if parts else "{category}"

        if not args.output:
            if sys.stdin.isatty():
                try:
                    args.output = (input("Output folder (required): ") or "").strip()
                except Exception:
                    args.output = None
            if not args.output:
                print("--sort requires --output")
                return 2

        if not args.input:
            if sys.stdin.isatty():
                try:
                    args.input = (input("Input folder (required): ") or "").strip()
                except Exception:
                    args.input = None
            if not args.input:
                print("--sort requires --input")
                return 2

        in_dir = Path(args.input).expanduser().resolve()
        out_dir = Path(args.output).expanduser().resolve()
        if not in_dir.exists() or not in_dir.is_dir():
            print(f"Not a directory: {in_dir}")
            return 2
        out_dir.mkdir(parents=True, exist_ok=True)

        core._ensure_model_loaded(status_cb=print)

        files = sorted([p for p in in_dir.iterdir() if p.is_file() and p.name.lower().endswith(core.IMAGE_EXT + core.VIDEO_EXT)])
        counts = {"images": 0, "videos": 0, "failed": 0}

        for p in files:
            try:
                low = p.name.lower()
                if low.endswith(core.VIDEO_EXT):
                    vids_dir = str(out_dir / "Videos")
                    if args.convert_videos:
                        base = os.path.splitext(p.name)[0]
                        mp4_path = core._unique_dest_path(vids_dir, base + ".mp4")
                        if args.dry_run:
                            print(f"[dry-run] {p.name} -> {mp4_path}")
                        else:
                            core.convert_video(str(p), mp4_path)
                            core._log_sort_destination_decision(
                                source_path=str(p),
                                category="Videos",
                                structure_pattern="Videos",
                                tokens={"category": "Videos"},
                                dest_dir=vids_dir,
                                dest_path=mp4_path,
                                flow="cli_video_convert",
                            )
                    else:
                        dest = core._unique_dest_path(vids_dir, p.name)
                        if args.dry_run:
                            print(f"[dry-run] {p.name} -> {dest}")
                        else:
                            shutil.copy2(str(p), dest)
                            core._log_sort_destination_decision(
                                source_path=str(p),
                                category="Videos",
                                structure_pattern="Videos",
                                tokens={"category": "Videos"},
                                dest_dir=vids_dir,
                                dest_path=dest,
                                flow="cli_video_copy",
                            )
                    counts["videos"] += 1
                elif low.endswith(core.IMAGE_EXT):
                    img = core.load_image_for_ai(str(p))
                    cat, _, _ = core._predict_category_internal(str(p), pil_img=img)
                    toks = core._structure_tokens(cat, str(p), img)
                    dest_dir = core._render_structure(str(out_dir), pattern, toks)
                    dest = core._unique_dest_path(dest_dir, p.name)
                    if args.dry_run:
                        print(f"[dry-run] {p.name} -> {dest}")
                    else:
                        shutil.copy2(str(p), dest)
                        core._log_sort_destination_decision(
                            source_path=str(p),
                            category=cat,
                            structure_pattern=pattern,
                            tokens=toks,
                            dest_dir=dest_dir,
                            dest_path=dest,
                            flow="cli_image",
                        )
                    counts["images"] += 1
            except Exception as e:
                print(f"Failed: {p.name}, {e}")
                counts["failed"] += 1

        print(f"Images categorized: {counts['images']}")
        print(f"Videos handled: {counts['videos']}")
        print(f"Failed items: {counts['failed']}")
        return 0

    if args.input and args.output:
        os.environ["MEDIASORTER_AUTORUN_INPUT"] = args.input
        os.environ["MEDIASORTER_AUTORUN_OUTPUT"] = args.output
        if args.interactive:
            os.environ["MEDIASORTER_AUTORUN_INTERACTIVE"] = "1"
        if args.convert_videos:
            os.environ["MEDIASORTER_AUTORUN_CONVERT_VIDEOS"] = "1"

    app = QApplication([sys.argv[0]])
    window = MediaSorter()
    window.show()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
