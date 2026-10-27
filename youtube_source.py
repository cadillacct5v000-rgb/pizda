"""YouTube captions/audio with explicit local authentication and atomic outputs."""
import html
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import parse_qs, urlsplit

import requests
import netutil


class YouTubeError(Exception):
    pass


class TimedSession(requests.Session):
    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", 30)
        return super().request(method, url, **kwargs)


def video_id(url):
    value = (url or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value
    parsed = urlsplit(value if "://" in value else "https://" + value)
    host = (parsed.hostname or "").lower()
    parts = parsed.path.strip("/").split("/")
    candidate = ""
    if host == "youtu.be":
        candidate = parts[0]
    elif host in ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "www.youtube-nocookie.com"):
        candidate = parse_qs(parsed.query).get("v", [""])[0]
        if len(parts) == 2 and parts[0] in ("shorts", "live", "embed"):
            candidate = parts[1]
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
        return candidate
    raise YouTubeError("Не похоже на ссылку YouTube.")


def error_hint(exc):
    low = str(exc).lower()
    if any(s in low for s in ("sign in", "sign-in", "cookies", "confirm you're not a bot", "login required")):
        return ("YouTube требует вход или проверку пользователя. Откройте ролик в своём браузере, "
                "войдите в YouTube и выберите этот браузер в настройке «Доступ к YouTube». "
                "Можно выбрать локальный файл cookies.txt. Доступ к закрытому ролику должен быть у вашего аккаунта.")
    if any(s in low for s in ("proxy", "403 forbidden", "blocked", "429", "too many requests")):
        return "YouTube отклонил запрос с этой сети (ограничение IP, частоты запросов или прокси). Повторите позже в сети, где ролик открывается."
    if any(s in low for s in ("connection", "reset", "timed out", "resolve", "getaddrinfo")):
        return "Соединение с YouTube прервалось. Проверьте, открывается ли ролик в браузере, и повторите загрузку."
    return str(exc).replace("\n", " ")[:500]


class QuietLogger:
    def debug(self, message): pass
    def warning(self, message): pass
    def error(self, message): pass


def options(cookie_file=None, browser=None):
    opts = {"quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True,
            "socket_timeout": 30, "retries": 2, "extractor_retries": 2,
            "logger": QuietLogger(), "progress_hooks": [lambda _: netutil.check_cancel()]}
    if cookie_file:
        path = Path(cookie_file).expanduser()
        if not path.is_file():
            raise YouTubeError("Файл cookies не найден. Выберите его заново в настройке доступа к YouTube.")
        opts["cookiefile"] = str(path)
    elif browser:
        if browser not in ("chrome", "firefox", "safari", "edge", "brave"):
            raise YouTubeError("Неподдерживаемый браузер для доступа к YouTube.")
        opts["cookiesfrombrowser"] = (browser,)
    for name in ("deno", "node"):
        executable = shutil.which(name)
        if not executable:
            executable = next((str(p) for base in ("/opt/homebrew/bin", "/usr/local/bin")
                               if (p := Path(base) / name).is_file() and os.access(p, os.X_OK)), None)
        if executable:
            opts["js_runtimes"] = {name: {"path": executable}}
            break
    return opts


def caption_text(data, extension):
    if extension == "json3":
        payload = json.loads(data)
        lines = ["".join(s.get("utf8", "") for s in e.get("segs", [])) for e in payload.get("events", [])]
    else:
        lines = []
        for line in data.splitlines():
            line = line.strip()
            if not line or "-->" in line or line.isdigit() or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
                continue
            lines.append(re.sub(r"<[^>]+>", "", line))
    result = []
    for line in lines:
        line = re.sub(r"\s+", " ", html.unescape(line)).strip()
        if line and not re.fullmatch(r"\[[^\]]+\]", line) and (not result or result[-1] != line):
            result.append(line)
    return " ".join(result).strip()


def transcript(url, progress=None, *, cookie_file=None, browser=None):
    from youtube_transcript_api import YouTubeTranscriptApi
    import yt_dlp
    vid = video_id(url)
    errors = []
    if progress:
        progress("загружаю субтитры YouTube")
    netutil.check_cancel()
    try:
        with TimedSession() as session:
            tracks = sorted(YouTubeTranscriptApi(http_client=session).list(vid), key=lambda t: t.is_generated)
            for track in tracks:
                netutil.check_cancel()
                try:
                    fetched = track.fetch()
                    text = " ".join(s.text.replace("\n", " ") for s in fetched if s.text)
                    if text.strip():
                        return text.strip()
                except Exception as exc:
                    netutil.check_cancel()
                    errors.append(error_hint(exc))
    except Exception as exc:
        netutil.check_cancel()
        errors.append(error_hint(exc))
    if progress:
        progress("проверяю субтитры через загрузчик YouTube")
    opts = options(cookie_file, browser)
    opts.update(skip_download=True, ignore_no_formats_error=True)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False) or {}
            for group in ("subtitles", "automatic_captions"):
                for language, formats in (info.get(group) or {}).items():
                    if language == "live_chat":
                        continue
                    for fmt in sorted(formats, key=lambda f: f.get("ext") != "json3"):
                        if fmt.get("ext") not in ("json3", "vtt", "srt") or not fmt.get("url"):
                            continue
                        netutil.check_cancel()
                        try:
                            with ydl.urlopen(fmt["url"]) as response:
                                text = caption_text(response.read().decode("utf-8-sig"), fmt["ext"])
                            if text:
                                return text
                        except Exception as exc:
                            netutil.check_cancel()
                            errors.append(error_hint(exc))
    except Exception as exc:
        netutil.check_cancel()
        errors.append(error_hint(exc))
    raise YouTubeError("Не удалось получить субтитры. " + (errors[-1] if errors else "У ролика нет доступных дорожек субтитров."))


def audio(url, out_dir, ffmpeg_dir, progress=None, *, cookie_file=None, browser=None):
    import yt_dlp
    from media_tools import ffmpeg_bin, run_media
    vid = video_id(url)
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f"yt_{vid}.m4a"

    def valid(path):
        if not path.is_file() or not path.stat().st_size:
            return False
        result = run_media([ffmpeg_bin("ffprobe"), "-v", "error", "-select_streams", "a:0",
                            "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)])
        return result.returncode == 0 and result.stdout.strip() == b"audio"

    if valid(output):
        return str(output)
    if progress:
        progress("субтитры не получены — скачиваю звук для распознавания")
    opts = options(cookie_file, browser)
    with tempfile.TemporaryDirectory(prefix=f".yt_{vid}_", dir=directory) as temporary:
        candidate = Path(temporary) / "audio.m4a"
        opts.update(format="bestaudio[ext=m4a]/bestaudio/best", ffmpeg_location=ffmpeg_dir,
                    outtmpl=str(Path(temporary) / "audio.%(ext)s"),
                    postprocessors=[{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}])
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([f"https://www.youtube.com/watch?v={vid}"])
        except Exception as exc:
            netutil.check_cancel()
            raise YouTubeError("Не удалось скачать звук. " + error_hint(exc)) from exc
        netutil.check_cancel()
        if not valid(candidate):
            raise YouTubeError("Загрузчик не создал пригодный аудиофайл. Незавершённые файлы не используются.")
        os.replace(candidate, output)
    return str(output)
