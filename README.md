# Video Flow 0.6.4

Fixes the Claude5.5 request: optional temperature is omitted because the current public OpenRouter model catalog does not advertise that sampling parameter. A regression test using recorded public capability metadata fails for the previous request and passes for this version. Actual paid inference on a customer account was not performed; HTTP422 can have other causes.

Provider400/422 errors now show a known field name and a locally authored rejection category. Raw upstream payloads, input, narration and credentials remain hidden; reading the error body is bounded. A billable POST is never retried automatically.

A first-replica voice preview and its player are visible on the voice stage. Only that block is submitted; remaining blocks stay untouched, and a ready preview is reused. Draft-to-voice navigation preserves pending edits. Script editing through Claude is explicitly optional for an already prepared script.

88 automated tests, TypeScript/lint/build, browser smoke with one-block voice preview, and actual local MP4 E2E passed using mock paid services. Windows workflow verifies fresh install, system Edge MP4, upgrade from0.6.3, preservation of data/config, and rollback. Its actual run result determines Windows acceptance.

Update: finish active generation tasks, then run Apply Update.bat in the existing installation and choose VideoFlow-0.6.4-Windows-x64.zip. For a new installation, extract fully and run Install Video AI Auto.bat. The source, lockfile, ready frontend and verified Node archive are included. No user data, keys, uploaded reports, production assets or node_modules are included.
