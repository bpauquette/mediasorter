# Developer Setup

## Local Run

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe mediasorter.py
```

## CLI Entry

```powershell
.\.venv\Scripts\python.exe mediasorter_cli.py --help
```

## Optional CLIP Runtime

```powershell
.\.venv\Scripts\python.exe mediasorter_cli.py --ai-provider clip_local --install-ai-provider
.\.venv\Scripts\python.exe mediasorter_cli.py --ai-provider clip_local --download-model
```

## Tests

```powershell
py -3 -m unittest discover -s tests -p "test_*.py" -v
```

## Windows Bundle

```powershell
.\build_windows_bundle.cmd --clean --standalone
```

## Windows Installer (NSIS)

```powershell
.\build_windows_installer.cmd --clean
```

With payment URL:

```powershell
.\build_windows_installer.cmd --clean --payment-url "https://your-checkout-link"
```

## Useful Environment Variables

- `MEDIASORTER_SUPPORT_URL`
- `MEDIASORTER_PAYMENT_URL`
- `MEDIASORTER_TRIAL_LIMIT`
- `MEDIASORTER_SHOW_ADVANCED`
