# MediaSorter Product Backlog

Date: March 7, 2026

This file is the working task record for product, UX, legal, marketing, and monetization work.
It replaces the previous "discussed but not captured" state.

## Goal

Ship a version of MediaSorter that:

- feels trustworthy and easy to use for non-technical Windows users
- has a clearer conversion path from trial to paid
- is commercially viable without handing away an unnecessary amount of revenue
- is safer on privacy, refund, and marketing claims

## Revenue Strategy Decision

Preferred path for initial launch:

- Use `Stripe Payment Links` for checkout and stop defaulting to Gumroad.
- Keep the app local-first and sell the Windows installer plus support/convenience.
- Deliver the installer through your own controlled link after payment.

Why:

- Stripe Payment Links are included with Stripe's standard payments pricing, which starts at `2.9% + 30c`.
- Gumroad currently charges `10% + 50c` on direct sales.
- On a `$19` sale, that is roughly:
  - Gumroad: `~$16.60` net before tax/compliance overhead
  - Stripe: `~$18.15` net before tax/compliance overhead
  - Delta: `~$1.55` more per sale with Stripe

Tradeoff:

- Stripe is not a merchant of record. You own tax collection/compliance, refund handling, and policy hygiene.
- If international consumer sales are a near-term priority, use a lower-fee merchant-of-record option instead of Gumroad.

Fallback if you want tax/compliance handled for you:

- Lemon Squeezy: `5% + 50c`, merchant of record, with some extra edge-case fees
- Paddle: `5% + 50c`, merchant of record

Decision to make now:

- Choose one:
  - `US-first + lowest fee`: Stripe Payment Links
  - `Global-first + lower operational burden`: Lemon Squeezy or Paddle

## Highest-Priority Tasks

1. Priority one: fix AI explanation quality so MediaSorter stops showing generic fallback text when users expect a real description of the picture.
2. Replace the current monetization recommendation in docs from Gumroad-first to a chosen payment strategy.
3. Redesign the main UI so the first-run path is understandable without reading documentation.
4. Publish customer-facing legal pages before asking anyone to pay.
5. Build a trial-to-paid funnel that feels trustworthy instead of improvised.
6. Validate the new UX with 3-5 non-technical users before pushing for sales.

## AI Explanation Quality Backlog

Captured criticism on March 8, 2026:

- The AI-generated text is often a generic fallback instead of a real description of what is in the photo.
- The classifier can make obviously wrong content calls, such as labeling a wooden outdoor deck photo as `pet`.
- The UI can make false factual claims by saying it "sees" objects or people that were never actually detected.
- `iphone screenshot` should be treated as a secondary/source classification after the app examines the screenshot content itself.
- Users need either:
  - a better explanation model that returns useful text fast enough for the UI, or
  - a workflow that waits a little longer when richer text is likely to arrive soon.
- This is priority one because generic AI text undermines trust in the product.
- New criticism: loading the AI runtime takes too long; startup latency needs its own optimization plan.

Evidence to verify and track:

- The current explanation path is rule-based and often built from category labels plus canned cue text, not from a dedicated image-description model.
- The current CLIP category prompts for `pet` are broad and aggressive (`pet`, `dog`, `cat`, `animal`) and there is no negative guardrail for obvious non-animal outdoor structures.
- The current review summary can restate category prompts as if they were observed facts, for example claiming `people indoors` when the selected category is `indoor photo`.
- The review UI also contains explicit fallback text such as:
  - `AI sees: visuals closest to ...`
  - `AI sees: visual pattern recorded.`
- Timeouts already allow very long waits for worker inference, so "wait longer" may not solve the actual problem if no caption request is being made.

Parallel workstreams:

- Workstream A: instrument explanation quality
  - log whether each explanation came from:
    - real model-generated description
    - category cue template
    - generic UI fallback
  - measure explanation latency separately from category latency
  - count how often generic fallback text reaches the UI
- Workstream B: improve the explanation pipeline
  - decide whether explanation text should come from:
    - the current classifier plus richer rationale generation
    - a dedicated local caption model
    - a hybrid fast-then-refine pipeline
  - add explicit explanation quality states in the UI:
    - `describing image`
    - `using quick category summary`
    - `detailed explanation ready`
- Workstream C: evaluate better local models
  - compare current CLIP-only flow against at least one real image-captioning model
  - measure:
    - first-result latency
    - explanation usefulness
    - CPU-only viability on Windows
    - memory footprint
  - decide whether to keep CLIP for classification and use a second model only for explanation text
- Workstream E: reduce obvious false positives
  - create a small regression set of clearly non-pet images that should never land in `pet`
  - inspect top-k category scores for those cases instead of trusting only the winner
  - add guardrails for categories with over-broad prompts, starting with `pet`
  - evaluate whether categories like `outdoor photo`, `landscape`, or a future `home/exterior` bucket should absorb these cases
- Workstream F: separate source classification from content classification
  - treat buckets like `iphone screenshot` and `facebook download` as source/context labels, not the primary semantic description
  - classify screenshot content first, then attach source metadata as a secondary label
  - update UI wording so source labels do not hide what the screenshot actually contains
- Workstream D: reduce AI runtime startup time
  - measure cold start vs warm start separately
  - break startup into:
    - venv/runtime bootstrap
    - worker process launch
    - model load
    - category/prototype sync
  - keep the worker alive between runs when safe
  - preload the preferred model after app launch or in the background after install
  - cache model state aggressively so the app avoids repeated cold loads
  - evaluate smaller/faster default models for first-run experience
  - consider a two-stage strategy:
    - fast startup model first
    - optional richer model after the app is already usable
  - expose startup diagnostics in the UI so users know whether the delay is:
    - first-time setup
    - normal model warmup
    - unexpected slow path

Concrete product decisions needed:

- Decide whether explanation quality is allowed to lag classification by a few seconds.
- Decide whether the UI should show a placeholder while waiting for richer text.
- Decide what maximum wait is acceptable before falling back to a simpler explanation.
- Decide whether richer explanations are required only for the live review panel or also for search/index records.
- Decide whether the default AI model should optimize for startup speed rather than peak quality.
- Decide whether the app should become usable immediately while AI warms in the background.

## UI Redesign Backlog

Current problem summary:

- The main window is overloaded.
- Too many advanced options are visible in the primary workflow.
- Several labels use product/internal language instead of user language.
- Too much important information is hidden in modal dialogs and incidental text.
- The app does not present a strong "what step am I on, what happens next, and what can go wrong" model.

Captured criticism on March 8, 2026:

- Keep hero copy literal and task-focused. Avoid vague phrases like `A calmer way to organize your photo library`.
- Folder-structure selection is not intuitive enough in the current GUI.
- Treat the folder-structure selector as suspect until it is covered by tests; verify preset choice, custom layout behavior, preview text, and summary sync.
- There is not enough visible confirmation of the selected folder structure after a user chooses an option such as `Location/Category`.
- Box 2 still looks like it has multiple competing text inputs; the folder-layout UI needs one obvious primary control and should only reveal custom text entry when it is actually needed.
- Too many panels are open at once; the GUI feels confusing and scattered.
- The app should start with a simple input/output folder chooser that remembers the last selections.
- Users should be able to reopen specific panels from menu items instead of seeing everything at once.
- The GUI needs a focus/menu system so users can work in a smaller, task-specific view such as setup, review, search, or tools.
- The in-app disk treemap should aim to beat SequoiaView on startup and especially on refresh speed; initial full scans are too slow.
- The treemap still has overlapping labels that are hard to read; label density needs to be drastically reduced.
- The treemap must use a more faithful squarified whole-drive layout; the current visual result still does not read like SequoiaView.
- Initial treemap scan speed is still unacceptable; pursue divide-and-conquer scanning with multiple independent directory workers.
- `Open Selected in Explorer` feels crude and slow; it should open parent folders for cleanup instead of trying to open the selected file directly.

### 1. Visibility of System Status

- Replace the single generic status label with a persistent run progress panel.
- Show:
  - current phase
  - file counts processed/remaining
  - estimated time remaining when possible
  - current output path
  - current AI/provider state
- Add clearer idle/loading/running/completed states to the main window.

### 2. Match Between the System and the Real World

- Rewrite UI copy in plain user language.
- Rename jargon-heavy actions such as:
  - `Run Face Identification On Existing Output`
  - `Forget My Last Classifications`
  - `Try-before-buy mode`
- Replace them with task-oriented labels users would naturally expect.
- Reorder the workflow to match the user's mental model:
  - choose photos
  - choose destination
  - choose organization style
  - review optional extras
  - run

### 3. User Control and Freedom

- Add obvious `Cancel` while running.
- Add `Pause` if practical.
- Add a dry-run / preview mode before large runs.
- Add a clearer "back out safely" path from interactive review.
- Add an explicit recovery path after accidental category choices.

### 4. Consistency and Standards

- Standardize button naming across the app.
- Standardize step headers and helper text style.
- Reduce inconsistent capitalization and verbose labels.
- Align primary and secondary actions visually and behaviorally.
- Use fewer modal dialogs for routine information.

### 5. Error Prevention

- Add a preflight checklist before the run starts:
  - missing input/output folder
  - output folder inside input folder
  - low free disk space
  - unavailable AI provider/model
  - inaccessible output destination
- Turn common warnings into inline blocking validation before processing begins.

### 6. Recognition Rather than Recall

- Replace memory-heavy labels with visible examples and presets.
- Add folder structure templates with examples users can click instead of composing mentally.
- Show category and folder outcomes before the run begins.
- Keep advanced settings hidden until explicitly expanded.
- Add automated GUI tests for:
  - preset selection updates the effective structure pattern
  - `Custom` enables manual layout entry
  - leaving `Custom` restores preset-driven behavior
  - structure preview and run summary stay in sync

### 7. Flexibility and Efficiency of Use

- Create `Basic` and `Advanced` modes instead of one crowded screen.
- Add saved run presets.
- Add a "repeat my last successful setup" action.
- Improve keyboard flow for frequent users.

### 8. Aesthetic and Minimalist Design

- Redesign the window around a simpler primary path.
- Move maintenance tools out of the core sorting screen.
- Separate setup, review, and maintenance into distinct views/panels.
- Reduce checkbox density and visual clutter.
- Improve spacing, grouping, and typography for scanability.

### 9. Help Users Recognize, Diagnose, and Recover from Errors

- Convert technical error dialogs into plain-language error states.
- For each common failure, include:
  - what happened
  - why it matters
  - what to do next
- Offer one-click recovery actions when possible.

### 10. Help and Documentation

- Add contextual help next to risky or confusing options.
- Create a task-based "first run" guide inside the app.
- Add a short "How MediaSorter organizes files" explanation with examples.
- Make help content searchable and task-focused.

## UX / Product Work Items

- Create low-fidelity wireframes for:
  - first-run onboarding
  - main sort flow
  - advanced settings
  - post-run summary
  - face identification flow
- Decide whether the app should become:
  - wizard-style, or
  - left-nav multi-page desktop app
- Run heuristic review against the redesigned UI before implementation.
- Run user tests with people who are not already familiar with the project.

## Identity Recognition Backlog

Current state:

- MediaSorter can classify photos into a general `pet` category.
- The existing face-clustering system is built for human faces and should remain a `People` feature for now.
- Specific dog/cat identification should be treated as a separate `Pets` feature, not folded into the current people database.

- Define the first supported scope:
  - dog-only
  - cat-and-dog
  - broader pet support
- Evaluate the least-risk technical path for pet identity:
  - generic image-embedding + user-labeled examples
  - pet-specific face/identity model
- Create a separate pet identity store instead of reusing the people database.
- Design a review flow for naming and merging pet identities after a run.
- Decide how much manual confirmation is required before MediaSorter claims a pet match.
- Add product language that clearly distinguishes:
  - `People grouping`
  - `Pet recognition`
- Collect a small real-world validation set with repeated photos of the same pets before shipping the feature.

## Monetization Backlog

- Decide payment stack:
  - Stripe Payment Links
  - Lemon Squeezy
  - Paddle
- Update `MONETIZATION_QUICKSTART.md` after the payment decision is made.
- Update `GUMROAD_SETUP_GUIDE.md` if Gumroad is no longer the preferred path.
- Add a proper post-payment fulfillment flow for installer delivery.
- Decide whether the paid offer is:
  - installer convenience only
  - installer + support
  - installer + updates for a period
- Clarify the paid value proposition in one sentence.
- Add pricing test plan:
  - `$19`
  - `$29`
  - `$39`
- Add one clear upgrade CTA inside the app, not several mixed surfaces.

## Distribution / Installer Backlog

Current state:

- A standalone NSIS installer path already exists:
  - `build_windows_installer.cmd`
  - `installer/windows/MediaSorter.nsi`
- The installer embeds support/payment/legal links and produces `dist\windows\MediaSorterSetup.exe`.
- There is not yet a full signing/release pipeline in the repo.

- Finish the standalone Windows installer path so it is the default release artifact.
- Make installer versioning derive from the app version instead of a hand-edited constant.
- Add a clean release checklist for:
  - bundle build
  - NSIS build
  - install/uninstall smoke test
  - upgrade-over-existing-version smoke test
  - clean-machine smoke test
- Verify that the installer includes everything required for non-technical users without Python installed.
- Review what is written into the installer:
  - first-run links
  - legal links
  - support/payment link
  - optional external tool references
- Decide whether the installer should default to machine-wide install, per-user install, or support both.
- Add release notes and installer metadata that look professional rather than developer-facing.

## Code Signing Backlog

- Decide signing strategy:
  - standard code-signing certificate
  - EV code-signing certificate
  - cloud/HSM-backed signing service
- Document the cost, operational burden, and SmartScreen tradeoffs of each signing path.
- Add signing support to the Windows release pipeline.
- At minimum, sign:
  - `dist\windows\MediaSorter.dist\mediasorter.exe`
  - `dist\windows\MediaSorterSetup.exe`
- Timestamp signatures during signing so binaries remain valid after certificate expiration.
- Add build variables for certificate thumbprint, timestamp URL, and signing tool location.
- Add a post-build verification step using Authenticode verification.
- Capture the exact release procedure so signing does not remain tribal knowledge.
- Decide how secrets/certificates will be stored:
  - local machine only
  - hardware token
  - cloud signing service
- Add a release-readiness check for Windows SmartScreen reputation behavior after signing.

## Legal / Compliance Backlog

This is operational guidance, not legal advice.

- Publish `PRIVACY.md`, `TERMS.md`, and `REFUND_POLICY.md` in finished, user-facing form.
- Link those documents from:
  - README
  - installer flow
  - in-app purchase/support surface
- Add explicit face-processing consent language before face clustering runs.
- Avoid accuracy claims that sound absolute or deceptive.
- Audit all bundled assets, icons, fonts, and dependencies for commercial redistribution rights.
- Decide how refunds will work in practice and who handles them.
- Document what data is stored locally and what, if anything, is sent to third parties.

## Marketing Backlog

- Rewrite the product positioning around one clear promise.
- Create a short landing page or sales page with:
  - headline
  - 3-5 proof points
  - screenshots
  - trial explanation
  - refund policy link
- Create before/after examples of messy library to organized output.
- Record a short demo video of a real sorting run.
- Publish a GitHub release page that supports conversion instead of reading like an internal build note.
- Add testimonials or short quotes after initial user testing.
- Decide the primary channel mix for launch:
  - GitHub
  - Reddit
  - photography/home-media communities
  - YouTube demo

## Immediate Next Steps

1. Audit the current explanation pipeline end-to-end and label every generic fallback path.
2. Add telemetry/tests that distinguish real image descriptions from template/fallback text.
3. Prototype one better explanation path:
   - wait slightly longer for richer text, or
   - add a dedicated caption model, whichever is technically justified by the audit.
4. Fix and test the folder-structure selection flow in the GUI.
5. Choose the payment approach: Stripe direct vs Lemon Squeezy/Paddle.
6. Draft the new information architecture for the main UI.
7. Turn that draft into wireframes before touching implementation.
8. Finalize the standalone NSIS installer and signing plan.
9. Finalize legal docs before public sales.
10. Update monetization docs so the repo matches the actual launch strategy.

## Source Notes

- Nielsen Norman Group heuristics:
  - https://www.nngroup.com/articles/ten-usability-heuristics/
- Stripe Payment Links / pricing:
  - https://stripe.com/us/payments/payment-links
  - https://stripe.com/pricing
- Stripe Tax pricing:
  - https://stripe.com/tax/pricing
- Gumroad pricing:
  - https://gumroad.com/pricing
- Lemon Squeezy pricing:
  - https://www.lemonsqueezy.com/pricing
  - https://docs.lemonsqueezy.com/help/getting-started/fees
- Paddle pricing:
  - https://www.paddle.com/pricing
