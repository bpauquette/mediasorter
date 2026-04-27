# MediaSorter

MediaSorter is a Windows desktop app that organizes photos and videos into clean folder structures using local-first processing.

## Windows Elevation

- Development launches self-elevate on Windows before starting the app.
- Bundled Windows executables are built with a UAC manifest that requests administrator rights.

## Product Focus

- Fast media organization for large personal libraries
- Simple default workflow for non-technical users
- Basic mode by default with optional advanced controls for power users
- Face identification can run in pass one or later pass
- Built-in trial mode before purchase

## End Users

Start here:

- `USER_QUICK_START.md`

## Developers

Setup/build/testing:

- `DEVELOPER_SETUP.md`
- `PRODUCT_BACKLOG.md`

## Installer + Payment Link

Build installer:

```powershell
.\build_windows_installer.cmd --clean
```

Embed your checkout link in installer + in-app support button:

```powershell
.\build_windows_installer.cmd --clean --payment-url "https://your-checkout-link"
```

If you also run a live licensing service, embed its API base URL too:

```powershell
.\build_windows_installer.cmd --clean --payment-url "https://your-checkout-link" --license-api-url "https://licenses.example.com"
```

Env fallback:

- `MEDIASORTER_SUPPORT_URL`
- `MEDIASORTER_PAYMENT_URL`
- `MEDIASORTER_LICENSE_API_URL`

## Policy + Launch Docs

- `LEGAL_MARKETING_RECOMMENDATIONS.md`
- `GUMROAD_SETUP_GUIDE.md`
- `MONETIZATION_QUICKSTART.md`
- `PRIVACY.md` (template)
- `TERMS.md` (template)
- `REFUND_POLICY.md` (template)

## Repository Layout

- `mediasorter.py`: main entrypoint
- `mediasorter_cli.py`: CLI entrypoint
- `mediasorter_window.py`: Qt app workflow
- `mediasorter_core.py`: core logic, providers, processing
- `mediasorter_widgets.py`: Qt support widgets/dialogs
- `installer/windows/MediaSorter.nsi`: Windows installer definition
