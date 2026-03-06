# MediaSorter

Desktop photo/video sorter with optional runtime AI providers.

## What It Does

- Pick an input folder and an output folder
- Supports runtime AI providers:
  - `none` (heuristics-only, no heavy ML packages)
  - `clip_local` (local CLIP via `torch` + `open_clip_torch`)
- Optional interactive mode to confirm/override categories
- Learns from your confirmations (keeps per-category embedding prototypes)
- Optional video conversion to MP4 via HandBrakeCLI

## Run (Windows)

```powershell
# From the repo root:
.\run_gui.cmd
```

`run_gui.cmd` handles the runtime setup for you:

- creates `.venv` (Python 3.12) if missing
- installs/syncs dependencies
- launches the GUI

Manual setup (advanced only):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe mediasorter.py
```

Optional CLIP provider install:

```powershell
python mediasorter_cli.py --ai-provider clip_local --install-ai-provider
python mediasorter_cli.py --ai-provider clip_local --download-model
```

`clip_local` installs into an isolated runtime venv under app data (`runtimes/clip_local`)
and runs inference through a worker subprocess.

## Bundle (Windows 10/11)

```powershell
# From the repo root:
.\build_windows_bundle.cmd --clean --standalone
```

- Default output: `dist\windows\MediaSorter.dist`
- Optional single file build: `.\build_windows_bundle.cmd --onefile`
- Build requires Python 3.12 (`py -3.12`) and typically Visual Studio C++ Build Tools for Nuitka.
- Smoke checklist: `RELEASE_WINDOWS_SMOKETEST.md`

## Code Layout

- `mediasorter.py`: thin entrypoint
- `mediasorter_cli.py`: CLI parsing and command dispatch
- `mediasorter_window.py`: main Qt window and user workflow
- `mediasorter_widgets.py`: custom Qt widgets/dialogs (stacks view, people review)
- `mediasorter_core.py`: shared core logic (provider selection, model loading, categorization, metadata, workers, persistence)
- `ai_backend/`: optional AI provider requirements/config

## Tests

```powershell
py -3 -m unittest discover -s tests -p "test_*.py" -v
```

- Unit tests: `tests/unit`
- Integration tests: `tests/integration`

## App Data

MediaSorter stores these in an app data folder (use the in-app **Open App Data Folder** button):

- `categories.txt`
- `user_corrections.json`
- `category_prototypes.json`

If you previously had `categories.txt` / `user_corrections.json` next to `mediasorter.py`, the app will migrate them into the app data folder on first run.

## Notes

- CLIP provider first run may download model weights (still runs locally after that).
- If HandBrake isn't installed at the configured path, video conversion will fail (images will still sort).
