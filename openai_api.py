"""Обёртка над API OpenAI: распознавание речи (Whisper, таймкоды слов) и чат-модель.

Используется, когда у пользователя нет ключа fast-gen. Ключ читается из config.json (openai_api_key)
и никогда не печатается.
"""
import json
import math
import os
import subprocess
import tempfile
import time

import requests
import netutil

import endpoints
import provider_access
import model_reply
import chat_stream
import api_response


def BASE():
    return endpoints.base("openai")


def _chat_params(model, max_tokens, temperature):
    """gpt-5.x и o-серия не принимают max_tokens/temperature — у них max_completion_tokens и температура только 1."""
    m = (model or "").lower().rsplit("/", 1)[-1]
    if m.startswith(("gpt-5", "o1", "o3", "o4")):
        return {"max_completion_tokens": max_tokens}
    return {"max_tokens": max_tokens, "temperature": temperature}
CHUNK_SECONDS = 600           # Whisper принимает файлы до 25 МБ: режем аудио по 10 минут
DEFAULT_CHAT_MODEL = "gpt-4.1-mini"
TRANSCRIBE_MODEL = "whisper-1"  # единственная модель с таймкодами слов


class OpenAIError(RuntimeError):
    pass


class OpenAIClient:
    def __init__(self, api_key: str, timeout: int = 120, provider="openai", stream=False, progress=None):
        self.stream, self.progress = stream, progress
        self.provider = provider
        self.key = api_key
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {api_key}"
        self.timeout = timeout

    def _check(self, r: requests.Response):
        if r.status_code >= 400:
            raise OpenAIError(provider_access.failure(self.provider, r, self.key))
        return api_response.json_object(r, self.provider, "/models или API JSON", self.key)

    def check(self) -> str:
        """Проверка ключа: список моделей."""
        import time as _t
        for attempt in range(3):  # api.openai.com иногда отвечает не с первого раза
            netutil.check_cancel()
            try:
                j = self._check(self.s.get(endpoints.base(self.provider) + "/models", timeout=30))
                break
            except requests.exceptions.RequestException:
                if attempt == 2:
                    raise
                netutil.pause(2)
        ids = [m.get("id", "") for m in j.get("data", [])]
        has_whisper = any("whisper" in i for i in ids)
        return "OK" + ("" if has_whisper else " · внимание: модель whisper-1 не видна на этом ключе")

    def check_model(self, model):
        """Read-only credential/catalog check, never a paid JSON generation probe."""
        netutil.check_cancel()
        response = self.s.get(endpoints.base(self.provider) + "/models", timeout=(15, 60), allow_redirects=False)
        try:
            data = self._check(response)
            models = data.get("data")
            if not isinstance(models, list) or any(not isinstance(item, dict) for item in models):
                raise OpenAIError(f"{self.provider}: /models не вернул список моделей; ключ не проверен.")
            ids = {item.get("id") for item in models if isinstance(item.get("id"), str)}
            if model not in ids:
                alias = model.rsplit("/", 1)[-1]
                matches = [name for name in ids if name.rsplit("/", 1)[-1] == alias]
                if len(matches) != 1:
                    raise OpenAIError(f"{self.provider}: ключ принят, но {model} отсутствует в доступном каталоге. Выберите модель через «Модели с сервера».")
            return f"OK · {self.provider} · ключ принят, {model} есть в каталоге. Генерация проверяется при запуске."
        finally:
            response.close()

    # --- чат ---
    def chat_json(self, system: str, user: str, model: str = None, max_tokens: int = 4000, temperature: float = 0.7) -> dict:
        if self.stream:
            return self._stream_chat_json(system, user, model or DEFAULT_CHAT_MODEL, max_tokens)
        last = None
        limit = max_tokens
        for attempt in range(4):
            netutil.check_cancel()
            r = None
            try:
                r = self.s.post(endpoints.base(self.provider) + "/chat/completions", json={
                    "model": model or DEFAULT_CHAT_MODEL,
                    **({"response_format": {"type": "json_object"}} if self.provider == "openai" or (model or "").lower().startswith(("gpt-", "openai/gpt-", "o1", "o3", "o4", "openai/o")) else {}),
                    **({"max_tokens": limit} if self.provider == "elysium" else _chat_params(model or DEFAULT_CHAT_MODEL, limit, temperature)),
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                }, timeout=300)
                if provider_access.exhausted(r):
                    raise OpenAIError(provider_access.failure(self.provider, r, self.key, model or DEFAULT_CHAT_MODEL))
                if r.status_code == 429 or r.status_code >= 500:
                    last = f"HTTP {r.status_code}: {r.text[:200]}"
                    netutil.pause(10 * (attempt + 1))
                    continue
                if r.status_code >= 400:
                    raise OpenAIError(provider_access.failure(self.provider, r, self.key, model))
                choice = model_reply.choice(r, self.key, self.progress)
                if choice.get("finish_reason") == "length" and limit < 32000:
                    limit = min(32000, limit * 2)  # ответ обрезан — повторяем с большим лимитом
                    last = f"ответ не влез в лимит, повторяю с {limit} токенами"
                    continue
                return model_reply.object_from_choice(choice, self.key)
            except chat_stream.StreamError as error:
                raise OpenAIError("Поток ответа API прерван; платный запрос автоматически не повторяется. Готовые пачки сохранены.") from error
            except (requests.RequestException, json.JSONDecodeError, KeyError) as e:
                last = str(e)
                netutil.pause(5 * (attempt + 1))
            finally:
                if r is not None:
                    r.close()
        raise OpenAIError(f"чат-модель не ответила корректно: {last}")

    def _stream_chat_json(self, system, user, model, max_tokens):
        """One billable POST per storyboard batch; never replay ambiguous failures."""
        import chat_stream
        from model_json import extract_object
        started = time.monotonic()
        response = None
        payload = {"model": model, "stream": True,
                   **({"max_tokens": max_tokens} if self.provider == "elysium" else _chat_params(model, max_tokens, .7)),
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if self.provider == "openai" or model.lower().startswith(('gpt-', 'openai/gpt-', 'o1', 'o3', 'o4', 'openai/o')):
            payload['response_format'] = {'type': 'json_object'}
        netutil.check_cancel()
        try:
            if self.progress:
                self.progress('ожидаю подключение к модели; один запрос текущей пачки')
            response = self.s.post(endpoints.base(self.provider) + '/chat/completions', json=payload, stream=True, timeout=(15,300))
            if response.status_code >= 400:
                raise OpenAIError(provider_access.failure(self.provider, response, self.key, model))
            content_type = str((getattr(response, 'headers', None) or {}).get('Content-Type', '')).lower()
            selected = model_reply.choice(response, self.key, self.progress)
            return model_reply.object_from_choice(selected, self.key)
        except json.JSONDecodeError as error:
            raise OpenAIError('Модель вернула некорректный JSON раскадровки. Готовые пачки сохранены; автоматического повтора нет.') from error
        except requests.RequestException as error:
            from quota_info import clean
            elapsed = max(0,round(time.monotonic()-started))
            detail = clean(str(error),(self.key,),220)
            raise OpenAIError(f'{self.provider} · {model} · {type(error).__name__} · {elapsed} с. {detail}. '
                              'Запрос текущей пачки автоматически не повторён; результат и списание неизвестны. Готовые пачки сохранены.') from error
        finally:
            if response is not None:
                response.close()

    def see_json(self, system: str, user: str, image_path: str, model: str = None, max_tokens: int = 600, base: str = None) -> dict:
        """Вопрос по картинке, ответ JSON. base — свой адрес (OpenRouter и другие совместимые сервисы)."""
        from llm_api import image_b64, extract_json
        data = image_b64(image_path)
        last = None
        m = model or DEFAULT_CHAT_MODEL
        for attempt in range(3):
            netutil.check_cancel()
            r = None
            try:
                r = self.s.post((base or endpoints.base(self.provider)) + "/chat/completions", json={
                    "model": m, **({"response_format": {"type": "json_object"}} if m.lower().startswith(("gpt-", "openai/gpt-")) else {}), **({"max_tokens": max_tokens} if self.provider == "elysium" else _chat_params(m, max_tokens, 0.7)),
                    "messages": [{"role": "system", "content": system + "\nОтвечай только JSON, без пояснений и без markdown."},
                                 {"role": "user", "content": [{"type": "text", "text": user},
                                                              {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + data}}]}],
                }, timeout=120)
                if provider_access.exhausted(r):
                    raise OpenAIError(provider_access.failure(self.provider, r, self.key, model or DEFAULT_CHAT_MODEL))
                if r.status_code == 429 or r.status_code >= 500:
                    last = f"HTTP {r.status_code}: {r.text[:200]}"
                    netutil.pause(5 * (attempt + 1))
                    continue
                if r.status_code >= 400:
                    raise OpenAIError(provider_access.failure(self.provider, r, self.key, m))
                return model_reply.object_from_choice(model_reply.choice(r, self.key, self.progress), self.key)
            except chat_stream.StreamError as error:
                raise OpenAIError("Поток ответа API прерван; запрос с картинкой автоматически не повторяется.") from error
            except (requests.RequestException, json.JSONDecodeError, KeyError, IndexError) as e:
                last = str(e)
                netutil.pause(3)
            finally:
                if r is not None:
                    r.close()
        raise OpenAIError(f"модель не ответила по картинке: {last}")

    # --- распознавание речи ---
    def _transcribe_file(self, path: str, language: str = None) -> dict:
        data = {"model": TRANSCRIBE_MODEL, "response_format": "verbose_json", "timestamp_granularities[]": "word"}
        if language:
            data["language"] = language
        last = ""
        for attempt in range(6):
            netutil.check_cancel()
            try:
                with open(path, "rb") as f:
                    r = self.s.post(endpoints.base(self.provider) + "/audio/transcriptions", data=data,
                                    files={"file": (os.path.basename(path), f, "audio/mpeg")}, timeout=600)
            except requests.RequestException as e:
                last = str(e)[:200]
                netutil.pause(15 * (attempt + 1))
                continue
            if r.status_code == 429 and "insufficient_quota" in r.text:
                raise OpenAIError("на ключе OpenAI закончилась квота или не привязана карта (insufficient_quota). "
                                  "Пополните баланс на platform.openai.com или выберите другой распознаватель.")
            if r.status_code == 401:
                raise OpenAIError("ключ OpenAI не принят (401). Проверьте ключ.")
            if r.status_code == 429 or r.status_code >= 500:
                last = f"{r.status_code}: {r.text[:200]}"
                netutil.pause(15 * (attempt + 1))
                continue
            return self._check(r)
        raise OpenAIError(f"сервис распознавания OpenAI не ответил после 6 попыток ({last})")

    def transcribe(self, audio_path: str, ffmpeg: str, ffprobe: str, duration: float, language: str = None,
                   progress=None) -> dict:
        """Возвращает {"text": ..., "words": [{"text","start","end"}]}. Длинное аудио режется на куски."""
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        n_chunks = max(1, math.ceil(duration / CHUNK_SECONDS))
        words, texts = [], []
        with tempfile.TemporaryDirectory() as tmp:
            for k in range(n_chunks):
                start = k * CHUNK_SECONDS
                if n_chunks == 1 and os.path.getsize(audio_path) < 24 * 1024 * 1024:
                    part = audio_path
                else:
                    part = os.path.join(tmp, f"part{k:03d}.mp3")
                    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-ss", str(start), "-t", str(CHUNK_SECONDS),
                                    "-i", audio_path, "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", part],
                                   check=True, capture_output=True, creationflags=no_window)
                if progress:
                    progress("transcribe", k, n_chunks, f"распознаю речь (OpenAI Whisper), часть {k + 1} из {n_chunks}")
                j = self._transcribe_file(part, language)
                texts.append((j.get("text") or "").strip())
                for w in j.get("words") or []:
                    words.append({"text": w.get("word", "").strip(), "start": float(w["start"]) + start,
                                  "end": float(w["end"]) + start})
        # Whisper отдаёт слова без знаков препинания — восстанавливаем их из текста, чтобы резать по фразам
        words = _attach_punctuation(words, " ".join(texts))
        return {"text": " ".join(texts), "words": words}


def _attach_punctuation(words, text):
    """Сопоставляем слова с текстом и переносим знаки препинания в конец слов."""
    import re
    tokens = text.split()
    ti = 0
    out = []
    for w in words:
        core = re.sub(r"[^\w]", "", w["text"]).lower()
        matched = None
        for look in range(ti, min(ti + 4, len(tokens))):
            if re.sub(r"[^\w]", "", tokens[look]).lower() == core and core:
                matched = tokens[look]
                ti = look + 1
                break
        out.append({"text": matched if matched else w["text"], "start": w["start"], "end": w["end"]})
    return out
