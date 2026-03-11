# MediaSorter Legal + Marketing Recommendations

Date: March 6, 2026  
Region focus: United States  
This is operational guidance, not legal advice.

## 1) Legal Risk Checklist (Fast, Practical)

### A. Sales and advertising disclosures

- Keep pricing, refund terms, and what users receive clearly visible before purchase.
- Avoid vague claims like "perfect sorting" or "100% face recognition accuracy."
- Prefer specific claims: "Local desktop sorter for photos/videos with optional face clustering."

Why:

- U.S. FTC truth-in-advertising and digital disclosure expectations emphasize clear, conspicuous disclosures and non-deceptive claims.

### B. Refund and support policy

- Publish a short refund policy on checkout page and README.
- Keep it simple and enforceable (example: "7-day refund window for installer purchase issues").
- State exactly where support is provided (email/GitHub issues) and expected response target.

### C. Privacy policy (required if collecting any personal data)

- Even if fully local, publish a policy saying:
  - what data is processed,
  - what is stored locally,
  - whether any data is sent to third-party services.
- If no cloud upload by default, say that explicitly.

### D. Face recognition / biometric considerations

- If you process/store face embeddings or clusters, treat that as sensitive data.
- Keep face processing local by default.
- Add explicit user opt-in language for face scan features.
- Do not upload face data to cloud without explicit separate consent.

State-law watch-outs:

- Illinois BIPA (biometric privacy law).
- Texas biometric identifier law (CUBI/Texas Business & Commerce Code Chapter 503).

### E. Tax handling (fastest path)

- Use merchant-of-record style checkout tooling behavior where possible (Gumroad simplifies tax/VAT handling operationally).
- Do not make tax-compliance promises you cannot verify.

### F. Licensing and IP hygiene

- Add a clear project license file (MIT or similar) if not already present.
- Keep third-party attributions/notices for bundled dependencies.
- Ensure icon/fonts/assets are licensed for commercial redistribution.

## 2) Marketing Improvements for Better Conversion

## Positioning

Use one clear promise:

- "Sort massive photo libraries locally on Windows in minutes, not days."

Secondary proof points:

- Local-first processing
- Optional face clustering
- Flexible folder structures (year/category/location)
- Windows installer and no Python required for users

## Offer design (simple)

- Free trial mode in app (already implemented): process first N items.
- Paid "full run" support link in app + installer.
- Single paid SKU first (avoid multiple confusing tiers).

Recommended starting price:

- $19 one-time for installer convenience + support access.

## Checkout copy (use immediately)

- Headline: "MediaSorter for Windows - Fast Local Photo Sorting"
- Bullets:
  - "Processes large libraries with category/date/location structure"
  - "Optional face clustering after run"
  - "Runs locally on your machine"
  - "Includes installer + update path"
- Guarantee line:
  - "If the installer does not run on your Windows machine, contact support for help or refund per policy."

## Conversion funnel

1. GitHub README + release page
2. "Try Before You Buy" checkbox in app
3. End-of-trial upgrade prompt
4. Hosted checkout (Gumroad/Stripe link)

## 3) Try-Before-You-Buy Options

Current built-in:

- GUI checkbox limits run size (default 200 items; configurable via `MEDIASORTER_TRIAL_LIMIT`).
- End-of-trial prompt opens `Support / Buy`.

Additional low-complexity options (optional):

- Trial presets in UI: 100 / 200 / 500.
- "Trial on one folder only" quick button.
- Post-trial report showing how many files remain to process.

Avoid initially:

- Complex license keys
- Online activation servers
- Aggressive DRM that increases support burden

## 4) Launch-Ready Action List (7 Days)

1. Publish `TERMS.md`, `PRIVACY.md`, and `REFUND_POLICY.md` in repo.
2. Add links to those docs in README and checkout page.
3. Keep public GitHub repo for trust/discoverability.
4. Publish installer release with embedded payment URL.
5. Run 3-user smoke tests (non-technical users) and improve wording from feedback.

## 5) Source Links (for policy grounding)

- FTC advertising + disclosures:
  - https://www.ftc.gov/business-guidance/advertising-marketing
  - https://www.ftc.gov/business-guidance/resources/dot-com-disclosures-information-about-online-advertising
- FTC shipping rule context:
  - https://www.ftc.gov/business-guidance/resources/selling-internet-prompt-delivery-rules
- Texas biometric law references:
  - https://statutes.capitol.texas.gov/Docs/BC/htm/BC.503.htm
  - https://www.texasattorneygeneral.gov/consumer-protection/file-consumer-complaint/consumer-rights/biometric-privacy
- Illinois biometric law references:
  - https://www.ilga.gov/legislation/publicacts/fulltext.asp?Name=095-0994
- California privacy rights overview:
  - https://oag.ca.gov/privacy/ccpa

