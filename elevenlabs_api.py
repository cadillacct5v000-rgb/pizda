"""Direct ElevenLabs TTS; a RoyalTechno key is a different credential."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess

import requests

import netutil
from media_tools import ffmpeg_bin, run_media
from tts_api import TTSError


class ElevenLabs:
    BASE = "https://api.elevenlabs.io"
    MODEL = "eleven_multilingual_v2"
    CHUNK = 4500

    def __init__(self, key):
        self.s = requests.Session()
        self.s.headers["xi-api-key"] = key

    def _check(self, response):
        if response.status_code < 400:
            return
        try:
            detail = response.json().get("detail") or {}
            code = detail.get("status", "") if isinstance(detail, dict) else ""
            message = detail.get("message", "") if isinstance(detail, dict) else str(detail)
        except (ValueError, AttributeError):
            code, message = "", "Сервис вернул ошибку."
        hints = {
            "invalid_api_key": "Ключ ElevenLabs не принят. Вставьте API-ключ из кабинета ElevenLabs.",
            "missing_permissions": "У ключа ElevenLabs не хватает прав: нужны Text to Speech и Voices (Read).",
            "quota_exceeded": "На аккаунте ElevenLabs закончились символы. Проверьте тариф и баланс.",
            "voice_not_found": "Голос недоступен этому аккаунту ElevenLabs. Выберите голос из «Списка».",
        }
        message = str(message).replace(self.s.headers["xi-api-key"], "[ключ скрыт]")
        hint = hints.get(code, message or "Проверьте ключ, права, баланс и выбранный голос.")
        raise TTSError(f"ElevenLabs: HTTP {response.status_code} {code}: {hint[:350]}")

    def _voice_page(self, **params):
        netutil.check_cancel()
        response = netutil.patient(lambda: self.s.get(self.BASE + "/v2/voices", params=params, timeout=(15, 60)))
        try:
            self._check(response)
            return response.json()
        finally:
            response.close()

    def check(self):
        self._voice_page(page_size=1)
        return "OK · ElevenLabs напрямую · голоса доступны; право синтеза и баланс проверяются при озвучке"

    def voices(self):
        out, token, seen = [], None, set()
        for _ in range(20):
            params = {"page_size": 100}
            if token:
                params["next_page_token"] = token
            page = self._voice_page(**params)
            for voice in page.get("voices") or []:
                vid = voice.get("voice_id")
                if not vid or vid in seen:
                    continue
                seen.add(vid)
                labels = voice.get("labels") or {}
                out.append({"id": vid, "name": voice.get("name") or vid,
                            "lang": labels.get("language", ""), "gender": labels.get("gender", "")})
            next_token = page.get("next_page_token")
            if not page.get("has_more") or not next_token or next_token == token:
                break
            token = next_token
        return out

    @staticmethod
    def _valid_audio(path):
        if not Path(path).is_file():
            return False
        result = run_media([ffmpeg_bin("ffprobe"), "-v", "error", "-select_streams", "a:0",
                            "-show_entries", "stream=codec_type:format=duration", "-of", "json", str(path)],
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            info = json.loads(result.stdout)
            duration = float(info["format"]["duration"])
            return (result.returncode == 0 and math.isfinite(duration) and duration > 0
                    and any(s.get("codec_type") == "audio" for s in info.get("streams", [])))
        except (ValueError, KeyError, TypeError):
            return False

    def synthesize(self, text, voice_id, out_path, progress=None):
        # No language_code: the official multilingual_v2 model does not support it.
        voice = (voice_id or "").strip()
        if not voice or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", voice):
            raise TTSError("ElevenLabs: выберите голос кнопкой «Список» или вставьте его Voice ID.")
        from pipeline import split_text
        parts = split_text(text, self.CHUNK)
        if not parts:
            raise TTSError("Нет текста для озвучки")
        identity = json.dumps([self.MODEL, voice, parts], ensure_ascii=False).encode("utf-8")
        directory = Path(out_path).parent / (".elevenlabs-" + hashlib.sha256(identity).hexdigest()[:24])
        directory.mkdir(exist_ok=True)
        files = []
        for index, part in enumerate(parts):
            netutil.check_cancel()
            destination = directory / f"{index:04d}.mp3"
            if not self._valid_audio(destination):
                if progress:
                    progress(f"озвучка ElevenLabs: часть {index + 1} из {len(parts)}")
                # A POST can charge the account even if its response is lost.
                # Do not retry it automatically after connection/read failures.
                response = self.s.post(self.BASE + f"/v1/text-to-speech/{voice}",
                                       params={"output_format": "mp3_44100_128"},
                                       json={"text": part, "model_id": self.MODEL},
                                       headers={"Accept": "audio/mpeg"}, stream=True, timeout=(15, 300))
                try:
                    self._check(response)
                    content_type = response.headers.get("Content-Type", "").lower()
                    if "json" in content_type or "text/" in content_type:
                        raise TTSError("ElevenLabs вернул сообщение вместо аудио; файл не принят.")
                    netutil.save_response(response, destination)
                finally:
                    response.close()
                if not self._valid_audio(destination):
                    destination.unlink(missing_ok=True)
                    raise TTSError("ElevenLabs вернул повреждённое аудио; файл не принят.")
            files.append(destination)
        netutil.check_cancel()
        if len(files) == 1:
            shutil.copyfile(files[0], out_path)
        else:
            # Relative fixed names avoid escaping arbitrary user directory names.
            (directory / "concat.txt").write_text("".join(f"file '{p.name}'\n" for p in files), encoding="utf-8")
            result = run_media([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                                "-i", "concat.txt", "-c:a", "libmp3lame", "-b:a", "128k", "-f", "mp3",
                                os.path.abspath(out_path)], cwd=directory,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode:
                raise TTSError("ElevenLabs: не удалось собрать части озвучки: " + result.stderr.decode(errors="replace")[:200])
        # Keep paid fragments on failure so a subsequent run can resume.
        if not self._valid_audio(out_path):
            raise TTSError("ElevenLabs: итоговая озвучка повреждена.")
        shutil.rmtree(directory)
        return out_path
