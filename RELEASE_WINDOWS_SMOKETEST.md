# Windows Release Smoke Test

Use this checklist for every Windows build before distribution.

## 1. Build Verification

1. Run `build_windows_bundle.cmd --clean --standalone`.
2. Confirm output exists at `dist\windows\MediaSorter.dist`.
3. Confirm output contains `MediaSorter.exe`.
4. Launch `MediaSorter.exe` directly from the bundle.

## 2. Environment Matrix

Run at least one smoke pass on each:

1. Windows 10 x64 (clean VM or fresh user profile)
2. Windows 11 x64 (clean VM or fresh user profile)

## 3. Functional Smoke Test

Prepare a sample input folder with:

1. 3-5 images (`.jpg`, `.png`)
2. 1 video (`.mov` or `.mp4`)

Then verify:

1. App opens without Python installed system-wide.
2. Input and output folder selection works.
3. `Start Processing` works in non-interactive mode.
4. Images are copied into category folders in output.
5. Video is copied into `Videos` when conversion is off.
6. Interactive mode allows category confirm/dismiss.
7. `Manage Categories` save/rename/remove works.

## 4. First-Run AI Behavior

1. On first run with internet, model download starts and completes.
2. After first run, run again with internet disabled.
3. Confirm cached model is used and sorting still works.

## 5. Optional Video Conversion Check

If HandBrakeCLI is expected to be supported:

1. Install HandBrakeCLI on test machine.
2. Enable `Convert Videos to MP4 using HandBrake`.
3. Confirm video conversion produces `.mp4`.
4. Confirm failures are surfaced clearly if HandBrake is missing.

## 6. App Data & Permissions

1. Confirm app creates user data directory and writes:
   `categories.txt`, `user_corrections.json`, `category_prototypes.json`.
2. Confirm app works under non-admin user account.
3. Confirm app runs from non-ASCII folder path (e.g. `C:\Users\<name>\Downloads\MediaSorter`).

## 7. Distribution Packaging

1. Zip `dist\windows\MediaSorter.dist` as release artifact.
2. Include `README.md` and a short `RUN_WINDOWS.txt` with launch steps.
3. Confirm `Support / Buy` opens the intended checkout URL from the installed app.
4. If the installer was built with `--license-api-url`, confirm activation reaches the live licensing API.
5. Record:
   - Git commit hash
   - Build machine OS version
   - Python version used for bundling
   - Build date (UTC)
