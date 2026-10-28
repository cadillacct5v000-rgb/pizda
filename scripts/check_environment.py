"""Check local runtime prerequisites without contacting external services."""
from pathlib import Path
import importlib
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from media_tools import ffmpeg_bin
    print(f"Python {platform.python_version()} · {platform.system()} · {platform.machine()}")
    if sys.version_info < (3, 12):
        raise RuntimeError("Требуется Python 3.12 или новее; проверенная версия — 3.12.")
    for module in ("requests", "PIL", "edge_tts", "yt_dlp", "yt_dlp_ejs", "youtube_transcript_api", "webview"):
        importlib.import_module(module)
    if sys.platform == "darwin":
        for module in ("objc", "Cocoa", "WebKit"):
            importlib.import_module(module)
    for tool in ("ffmpeg", "ffprobe"):
        path = ffmpeg_bin(tool)
        result = subprocess.run([path, "-version"], capture_output=True, text=True, check=True, timeout=15)
        print(result.stdout.splitlines()[0])
    encoders = subprocess.run([ffmpeg_bin(), "-hide_banner", "-encoders"], capture_output=True, text=True, check=True, timeout=15).stdout
    filters = subprocess.run([ffmpeg_bin(), "-hide_banner", "-filters"], capture_output=True, text=True, check=True, timeout=15).stdout
    for encoder in ("libx264", "aac", "libmp3lame"):
        if encoder not in encoders:
            raise RuntimeError(f"FFmpeg: отсутствует кодек {encoder}")
    for name in ("subtitles", "xfade"):
        if name not in filters:
            raise RuntimeError(f"FFmpeg: отсутствует фильтр {name}")
    for path in ("ui.html", "fonts/IntroDemo-BlackCAPS.otf", "docs/instruction.html"):
        if not (ROOT / path).is_file():
            raise RuntimeError(f"Отсутствует ресурс {path}")
    from youtube_source import javascript_runtime
    runtime = javascript_runtime()
    if runtime:
        result = subprocess.run([runtime[1], "--version"], capture_output=True, text=True, check=True, timeout=15)
        print("YouTube JavaScript: " + result.stdout.splitlines()[0])
    else:
        print("YouTube: Deno/Node не найден. Субтитры могут работать, скачивание звука требует JS runtime.")
    print("Локальные зависимости и ресурсы доступны. Сеть и окно здесь не проверяются.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Ошибка окружения: {exc}", file=sys.stderr)
        import os
        if os.environ.get("GITHUB_ACTIONS") == "true":
            message = str(exc).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print(f"::error title=Environment check::{message}")
        sys.exit(1)
