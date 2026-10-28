# Build on macOS using native Python (arm64 on Apple Silicon).
import sys
import platform
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

if sys.platform != 'darwin':
    raise SystemExit('Build KadroGen-macOS.spec on macOS.')

root = Path(SPECPATH)
datas = [(str(root / 'ui.html'), '.'), (str(root / 'fonts'), 'fonts'),
         (str(root / 'music'), 'music'), (str(root / 'docs/instruction.html'), 'docs')]
binaries, hiddenimports = [], []
for package in ('webview', 'youtube_transcript_api', 'edge_tts', 'yt_dlp', 'yt_dlp_ejs'):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis([str(root / 'app.py')], pathex=[str(root)], binaries=binaries,
             datas=datas, hiddenimports=hiddenimports, hookspath=[],
             hooksconfig={}, runtime_hooks=[], excludes=['pythonnet', 'clr_loader'],
             noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='KadroGen',
          debug=False, strip=False, upx=False, console=False,
          argv_emulation=False, target_arch=platform.machine(),
          codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='KadroGen')
app = BUNDLE(coll, name='KadroGen.app', bundle_identifier='org.kadrogen.desktop',
             version='1.8.0', info_plist={
                 'NSHighResolutionCapable': True,
                 'CFBundleDisplayName': 'Kadro.Gen',
             })
