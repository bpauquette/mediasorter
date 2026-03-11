# Gumroad Setup Guide For MediaSorter

Date: March 6, 2026  
Goal: start collecting payments quickly with minimal operational overhead.

## 1) Create/prepare your Gumroad account

1. Sign in to Gumroad.
2. Complete payout settings first (identity + payout destination), because payouts require verification.
3. Keep legal name/business info consistent with payout/tax records.

Reference:

- https://gumroad.com/help/article/260-your-payout-settings-page.html

## 2) Create your MediaSorter product

1. In Gumroad dashboard, create a new digital product.
2. Product name recommendation:
   - `MediaSorter Windows Installer`
3. Upload file:
   - `dist\windows\MediaSorterSetup.exe`
4. Set a simple price first (example: `$19` one-time).
5. Product description should include:
   - what it does
   - Windows compatibility
   - support contact method
   - refund policy summary

## 3) Configure checkout for conversion

1. Keep checkout copy short and specific.
2. Enable optional tipping if you want extra voluntary support.
3. Optionally configure upsells later (do not block launch for this).

References:

- https://gumroad.com/help/article/345-tipping.html
- https://gumroad.com/help/article/331-creating-upsells.html

## 4) Test purchase flow safely

Do not buy your own product with your personal card.  
Use Gumroad's test purchase flow while logged in.

Reference:

- https://gumroad.com/help/article/62-testing-a-purchase.html

## 5) Publish and link to installer flow

1. Copy your Gumroad product URL.
2. Rebuild installer embedding that URL:

```powershell
.\build_windows_installer.cmd --clean --payment-url "https://your-gumroad-product-url"
```

3. Publish installer in GitHub release and keep Gumroad link in:
   - installer shortcuts
   - app `Support / Buy` button
   - README

## 6) Operational notes (important)

- Gumroad may review accounts before first payout.
- Understand fees and merchant-of-record/tax handling behavior.
- Keep refund/support responses fast to reduce disputes/chargebacks.

References:

- https://gumroad.com/help/article/160-suspension.html
- https://gumroad.com/pricing
- https://gumroad.com/help/article/325-indirect-taxes-on-sales-via-discover.html

## 7) Suggested launch sequence (same day)

1. Build installer.
2. Upload to Gumroad product.
3. Run test purchase flow.
4. Publish product.
5. Publish GitHub release.
6. Announce with one clear CTA:
   - "Try free mode in app, then unlock full run via Support / Buy."
