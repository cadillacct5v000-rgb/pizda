# Video Flow 0.6.1 Windows x64 preview

Fixes installation hanging on the Claude HTTP test and launching a backend without a ready frontend. Runtime/SQLite resources are closed before removing Windows test folders. The ready frontend is included in this ZIP, versioned and checksum verified.

Close the old installer window and run Stop.bat. Back up data/config. Extract the new ZIP into the same application folder with replacement, keeping data/config, then run Install Video AI Auto.bat and wait for completion. Start now refuses an incomplete/failed installation and checks the actual homepage before opening a browser.

Includes three reference-based style variants, optional 5-second previews, selected style propagation, Nano Banana, Kling, Seedance and Claude via fal.ai; Lumean remains the voice service.

Verification: 53 automated tests, lint, types/build and browser flow passed locally. A separate Windows runner checks the package installer. Paid API output quality and a clean consumer Windows machine under its antivirus require separate acceptance. No user projects, credentials, reports or media are included.
