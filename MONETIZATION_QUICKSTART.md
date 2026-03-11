# MediaSorter Monetization Quickstart

Fastest path to getting paid with minimal engineering overhead.

## Recommended Model

- Sell the Windows installer through a hosted checkout page.
- Suggested platform: Gumroad (simple setup and file delivery).
- Keep the GitHub repo open, charge for the convenience installer package + support.

## 30-Minute Setup

1. Create a Gumroad product:
   - Name: `MediaSorter Windows Installer`
   - Price: start at `$19` (adjust later)
   - Upload: `dist\windows\MediaSorterSetup.exe`
2. Copy your Gumroad product URL.
3. Build installer with the payment URL embedded:

```powershell
.\build_windows_installer.cmd --clean --payment-url "https://your-gumroad-url"
```

4. Publish a GitHub Release and attach `MediaSorterSetup.exe`.
5. In release notes and README, include:
   - free/open-source repo link
   - paid installer/support link

The installer writes `support_url.txt` into the install directory, so the in-app
`Support / Buy` button points to your checkout URL automatically.

## In-App/Installer Surface

The project now exposes the support/payment URL in two places:

- App button: `Support / Buy`
- Installer shortcut: `Support and Buy MediaSorter`
- Trial conversion point: end-of-run upgrade prompt in `Try Before You Buy` mode

Both use:

- `MEDIASORTER_SUPPORT_URL`, or
- `MEDIASORTER_PAYMENT_URL`, or
- fallback: latest GitHub release page

## Pricing Starter

- Personal use: `$19`
- Family power user: `$39`
- One-time support call add-on: `$49` (30 minutes)

Keep pricing simple first, then optimize after real user feedback.

## Try Before You Buy Options

Current built-in option:

- GUI checkbox: `Try Before You Buy (process first N items)`
- Default limit: `200` items
- Override with env var: `MEDIASORTER_TRIAL_LIMIT`

Recommended default rollout:

1. Keep trial at `200` for first launch period.
2. Track user feedback/conversion by asking users where they found the product.
3. Increase to `300-500` only if support burden stays manageable.
