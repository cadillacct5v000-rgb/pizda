# -*- mode: python ; coding: utf-8 -*-
import os
import shutil
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

if sys.platform != 'win32':
    raise SystemExit('Build KadroGen.spec on Windows. Use KadroGen-macOS.spec on macOS.')

root = Path(SPECPATH)

def required_tool(name, directory):
    local = root / directory / name
    if local.is_file():
        return str(local.resolve())
    # Chocolatey's PATH entries can be launcher shims, which must never be
    # bundled: they would still point at the build machine's installation.
    package = 'deno' if name == 'deno.exe' else 'ffmpeg'
    chocolatey = Path(os.environ.get('ChocolateyInstall', r'C:\ProgramData\chocolatey'))
    actual = sorted((chocolatey / 'lib' / package).glob('**/' + name))
    found = str(actual[0]) if actual else shutil.which(name)
    if not found:
        raise SystemExit(f'Missing {name}. Run SETUP_WINDOWS.cmd before building.')
    found = Path(found).resolve()
    if found.parent == chocolatey / 'bin':
        raise SystemExit(f'{name} is a Chocolatey shim; put the real executable in {directory}/ before building.')
    return str(found)

datas = [(str(root / 'ui.html'), '.'), (str(root / 'fonts'), 'fonts'),
         (str(root / 'music'), 'music'), (str(root / 'docs/instruction.html'), 'docs')]
binaries = [(required_tool('ffmpeg.exe', 'ffmpeg'), 'ffmpeg'),
            (required_tool('ffprobe.exe', 'ffmpeg'), 'ffmpeg'),
            (required_tool('deno.exe', 'runtime'), 'runtime')]
hiddenimports = ['clr_loader', 'pythonnet']
tmp_ret = collect_all('webview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('clr_loader')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pythonnet')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('youtube_transcript_api')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('edge_tts')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('yt_dlp')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('yt_dlp_ejs')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    [str(root / 'app.py')],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='KadroGen',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(root / 'version_info.txt'),
    icon=[str(root / 'app.ico')],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='KadroGen',
)
