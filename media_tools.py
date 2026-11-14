"""Native FFmpeg discovery and interruptible media processes."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import netutil


def ffmpeg_bin(name="ffmpeg"):
    if name not in ("ffmpeg", "ffprobe"):
        raise ValueError(f"Unsupported media tool: {name}")
    from fastgen_api import app_dir
    executable = name + (".exe" if sys.platform == "win32" else "")
    roots = [getattr(sys, "_MEIPASS", app_dir()), app_dir()]
    candidates = [Path(root) / "ffmpeg" / executable for root in roots]
    if sys.platform == "darwin":
        # Current Homebrew's base ffmpeg omits libass; full is keg-only.
        candidates.extend(Path(root) / name for root in
                          ("/opt/homebrew/opt/ffmpeg-full/bin", "/usr/local/opt/ffmpeg-full/bin"))
    found = shutil.which(executable)
    if found:
        candidates.append(Path(found))
    # Finder-launched .app bundles do not inherit the user's shell PATH.
    if sys.platform == "darwin":
        candidates.extend(Path(root) / name for root in ("/opt/homebrew/bin", "/usr/local/bin"))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())
    hint = "brew install ffmpeg" if sys.platform == "darwin" else "установите FFmpeg и добавьте его в PATH"
    raise FileNotFoundError(f"Не найден {name}: {hint} (нужны ffmpeg и ffprobe).")


def run_media(command, *, cwd=None, creationflags=0, cancel_check=None):
    """Drain both pipes while polling cancellation; always reap killed children."""
    check = cancel_check or netutil.check_cancel
    check()
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, cwd=cwd, creationflags=creationflags) as proc:
        try:
            while True:
                check()
                try:
                    stdout, stderr = proc.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    continue
            check()
            return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)
        except BaseException:
            if proc.poll() is None:
                proc.kill()
            proc.communicate()
            raise


_VALID_VIDEOS = {}  # Bounded cache of verified local file revisions.


def valid_video(path):
    """Reject interrupted/HTML cache files without decoding or loading the whole clip."""
    try:
        path=Path(path);st=path.stat()
        if not st.st_size:return False
        identity=(str(path.resolve()),st.st_size,st.st_mtime_ns)
        if identity in _VALID_VIDEOS:return True
        result=run_media([ffmpeg_bin('ffprobe'),'-v','error','-select_streams','v:0',
                          '-show_entries','stream=codec_type','-of','csv=p=0',str(path)])
        valid=result.returncode==0 and result.stdout.strip()==b'video'
        if valid:
            if len(_VALID_VIDEOS)>=256:_VALID_VIDEOS.pop(next(iter(_VALID_VIDEOS)),None)
            _VALID_VIDEOS[identity]=True
        return valid
    except (OSError,ValueError):return False
