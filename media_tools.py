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
