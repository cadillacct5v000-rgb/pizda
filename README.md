# Video Flow 0.6.3 — browser installation recovery

Fixes installation failing when the Remotion browser download is interrupted with ECONNRESET. Existing Chrome/Edge are detected and actually launched headlessly before use. A verified path is saved in config/browser.json and used by Render and Studio. Remotion is downloaded only when no installed candidate works. Failed candidates never create a success receipt.

For an existing installation (including a failed 0.6.1 install with Node already present), run its Apply Update.bat and choose this ZIP. Successful verification recovers the old install.failed marker without editing API keys. For a new installation extract the entire ZIP and run Install Video AI Auto.bat.

82 automated tests, browser smoke and actual local MP4 E2E use mock paid providers. Windows workflow verifies the installed browser, fresh install, Unicode shortcut, MP4, upgrade from 0.6.1 with a failed-install marker, and rollback preserving a project and config. Its actual run result determines Windows acceptance; the presence of a workflow alone is not evidence. No live paid voice/image/video quality was evaluated.

The package includes ready frontend, source, lockfile and verified Node archive. No user data, uploaded logs, keys or node_modules are included.
