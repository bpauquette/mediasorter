# AI Backend Subproject

This folder holds optional AI provider runtimes that can be installed at runtime.

The base MediaSorter app now runs without heavyweight ML packages by default.

## Providers

- `none`: Heuristics-only mode (no extra packages).
- `clip_local`: Local CLIP provider (`torch` + `open_clip_torch`).

## Runtime Install

The GUI and CLI can install provider dependencies on demand.

CLI examples:

```powershell
python mediasorter_cli.py --list-ai-providers
python mediasorter_cli.py --ai-provider clip_local --install-ai-provider
python mediasorter_cli.py --ai-provider clip_local --download-model
```

Provider requirements are stored under `ai_backend/providers/<provider>/requirements.txt`.

`clip_local` is installed into an isolated runtime venv:

- `%LOCALAPPDATA%\MediaSorter\runtimes\clip_local\venv` (Windows)

Inference is served by a provider worker process in that runtime.

Current runtime bootstrap expects an available Python interpreter (`py -3.12` or `python`)
to create the provider venv when first installing a provider.
