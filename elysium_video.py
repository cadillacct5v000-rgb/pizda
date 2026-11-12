"""Elysium's asynchronous media API. Never retry a billable submission."""
import hashlib
import json
import math
import os
import re
import time
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
import netutil
from fastgen_api import data_dir
from native_projects import ProjectStore

MODEL = 'google/gemini-omni-flash-1.2'
KLING_MODEL = 'kling-3.0/video'
MODELS = (MODEL, KLING_MODEL)
DEFAULT_BASE = 'https://elysiumai.garden/v1'


class ElysiumVideoError(RuntimeError):
    pass


class ElysiumVideo:
    def __init__(self, key, base=None, resolution='720p', *, model=MODEL, kling_mode='std', store=None, session=None, poll=5, max_wait=1800):
        if not key:
            raise ElysiumVideoError('Введите ключ Elysium для оживления картинок.')
        self.base = (base or DEFAULT_BASE).rstrip('/')
        if urlparse(self.base).scheme != 'https':
            raise ElysiumVideoError('Для Elysium Video нужен адрес HTTPS.')
        if resolution not in ('720p', '1080p'):
            raise ElysiumVideoError('Выберите 720p или 1080p.')
        if model not in MODELS:
            raise ElysiumVideoError('Выберите поддерживаемую видеомодель Elysium.')
        if kling_mode not in ('std', 'pro'):
            raise ElysiumVideoError('Выберите Kling Standard или Pro.')
        self.model, self.kling_mode = model, kling_mode
        self.label = 'Kling 3.0' if model == KLING_MODEL else 'Gemini'
        self.resolution = resolution
        self._secret = key
        self.session = session or requests.Session()
        self.session.headers['Authorization'] = 'Bearer ' + key
        self.account = hashlib.sha256(key.encode()).hexdigest()
        self.store = store or ProjectStore(data_dir())
        self.poll, self.max_wait = poll, max_wait

    def _safe_error(self, value):
        text = str(value).replace(self._secret, '[ключ скрыт]')
        text = re.sub(r'(?i)Bearer\s+[^\s"<>]+', 'Bearer [скрыто]', text)
        text = re.sub(r'\bsk-[A-Za-z0-9_-]+', '[ключ скрыт]', text)
        text = re.sub(r'https?://[^\s"<>]+', '[ссылка скрыта]', text)
        text = re.sub(r'<[^>]*>', ' ', text)
        return ' '.join(text.split())[:700]

    def _json(self, response, context=''):
        if response.status_code >= 400:
            try:
                body = response.json()
                error = body.get('error') or body
                if isinstance(error, dict):
                    detail = ' · '.join(str(error[k]) for k in ('code', 'type', 'message') if isinstance(error.get(k), (str, int)))
                else:
                    detail = str(error)
            except Exception:
                detail = getattr(response, 'text', '')
            detail = self._safe_error(detail or 'Сервер не указал причину отказа.')
            phase = (' · ' + self._safe_error(context)) if context else ''
            raise ElysiumVideoError(f'Elysium Video HTTP {response.status_code}{phase}: {detail}')
        result = response.json()
        if not isinstance(result, dict):
            raise ElysiumVideoError('Elysium Video вернул некорректный ответ.')
        return result

    def _get(self, path):
        return self._json(netutil.patient(lambda: self.session.get(self.base + path, timeout=60)), 'GET ' + path)

    def _failure_detail(self, result):
        containers = [result]
        for name in ('metadata', 'data', 'result', 'details'):
            if isinstance(result.get(name), dict):
                containers.append(result[name])
        for field in ('error', 'failure_reason', 'error_message', 'fail_reason', 'failMsg', 'failure', 'reason', 'message', 'error_code'):
            for container in containers:
                value = container.get(field)
                if value:
                    return self._safe_error(json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value)
        def summary(value, depth=0):
            if depth > 4:
                return '[сокращено]'
            if isinstance(value, dict):
                private = {'input', 'prompt', 'request', 'authorization', 'api_key', 'key', 'token', 'base64', 'image', 'images', 'video'}
                return {k: summary(v, depth + 1) for k, v in list(value.items())[:30] if str(k).lower() not in private}
            if isinstance(value, list):
                return [summary(v, depth + 1) for v in value[:5]]
            return self._safe_error(value) if isinstance(value, str) else value
        return self._safe_error('Сервер вернул failed. Ответ: ' + json.dumps(summary(result), ensure_ascii=False))

    def check(self):
        # Authenticated GET; no generation or billing. Catalog presence is not a paid access test.
        self._get('/models')
        catalog = self._get('/media/models')
        model = next((m for m in catalog.get('data', []) if m.get('id') == self.model), None)
        if not model or not any(o.get('name') == 'generate' for o in model.get('operations', [])):
            raise ElysiumVideoError(f'В медиакаталоге Elysium нет {self.model}.')
        return f'Ключ принят; {self.model} есть в каталоге. Генерация не запускалась; права и списание проверяются при запуске клипа.'

    def generate(self, prompt, image, out_path, duration, progress=None):
        seconds = max(3, min(15, math.ceil(float(duration))))
        image_hash = hashlib.sha256(Path(image).read_bytes()).hexdigest()
        identity = [self.base, self.account, self.model, self.resolution if self.model == MODEL else self.kling_mode, seconds, prompt, image_hash]
        operation = 'elysium-video:' + hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        state = self.store.operation(operation) or {}
        if state.get('status') == 'failed' and not state.get('id'):
            raise ElysiumVideoError('Elysium отклонил этот клип. Исправьте движение или создайте новый вариант плана; автоматического платного повтора нет.')
        task = state.get('id')
        if not task:
            if state.get('status') in ('submitting', 'uncertain'):
                raise ElysiumVideoError('Отправка видео в Elysium не подтверждена. Проверьте историю в кабинете; повтор остановлен, чтобы не создать второе платное задание.')
            try:
                reference = json.loads(Path(str(image) + '.source.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                reference = {}
            url = reference.get('url', '')
            if reference.get('sha256') != image_hash or urlparse(url).scheme != 'https':
                raise ElysiumVideoError('У картинки нет исходной HTTPS-ссылки для Elysium. Создайте новый вариант этого плана через FastGen: старые локальные картинки без ссылки нельзя отправить в этот API.')
            inputs = {'prompt': prompt, 'duration': seconds, 'resolution': self.resolution,
                      'aspect_ratio': '16:9', 'first_frame_url': url}
            if self.model == KLING_MODEL:
                inputs = {'prompt': prompt, 'duration': str(seconds), 'image_urls': [url],
                          'aspect_ratio': '16:9', 'mode': self.kling_mode, 'sound': False,
                          'multi_shots': False, 'multi_prompt': []}
            body = {'model': self.model, 'operation': 'generate', 'prompt': prompt, 'input': inputs}
            netutil.check_cancel()
            if progress:
                progress(f'{self.label} через Elysium: отправляю новое видеозадание для этой картинки. Генерация оплачивается сервисом.')
            self.store.operation(operation, {'status': 'submitting'})
            try:
                response = self.session.post(self.base + '/videos', json=body, timeout=120)
                if 400 <= response.status_code < 500:
                    self.store.operation(operation, {'status': 'rejected'})
                    self._json(response, 'отправка нового задания POST /videos')
                result = self._json(response, 'отправка нового задания POST /videos')
                task = result.get('id')
                if not isinstance(task, str) or not task:
                    raise ValueError('missing task ID')
            except ElysiumVideoError:
                if self.store.operation(operation).get('status') != 'rejected':
                    self.store.operation(operation, {'status': 'uncertain'})
                raise
            except Exception:
                self.store.operation(operation, {'status': 'uncertain'})
                raise ElysiumVideoError('Elysium: ответ на отправку видео не подтверждён. Автоповтора нет; проверьте историю запросов в кабинете.') from None
            state = {'id': task, 'status': result.get('status', 'queued')}
            self.store.operation(operation, state)
        deadline = time.monotonic() + self.max_wait
        while time.monotonic() < deadline:
            netutil.check_cancel()
            if progress:
                progress(f'{self.label} через Elysium: проверяю статус задания {task}. Новая генерация не отправляется.')
            try:
                result = self._get('/videos/' + quote(task, safe=''))
            except ElysiumVideoError as error:
                raise ElysiumVideoError(f'{error} · ID задания {self._safe_error(task)} сохранён. Отказ при получении статуса, а не при отправке. Проверьте эту задачу в Elysium; повторной генерации нет.') from None
            status = result.get('status')
            state = {'id': task, 'status': status or 'unknown'}
            if status == 'failed':
                detail = self._failure_detail(result)
                state['error'] = detail
                self.store.operation(operation, state)
                raise ElysiumVideoError(f'Elysium не создал клип: {detail} · ID задания {self._safe_error(task)} сохранён. Новая генерация автоматически не отправляется; повторный запуск только проверит это же задание.')
            if status in ('cancelled', 'canceled'):
                state['status'] = 'failed'
                self.store.operation(operation, state)
                raise ElysiumVideoError('Видеозадание отменено в Elysium.')
            self.store.operation(operation, state)
            if status == 'completed':
                metadata = result.get('metadata') or {}
                url = metadata.get('url', '')
                if urlparse(url).scheme != 'https':
                    raise ElysiumVideoError('Задание готово, но Elysium не вернул HTTPS-ссылку в metadata.url. ID сохранён.')
                # Never send Elysium's Bearer token to a media/CDN URL.
                temporary = str(out_path) + '.part.mp4'
                try:
                    with requests.get(url, stream=True, timeout=300) as response:
                        netutil.save_response(response, temporary)
                    from media_tools import ffmpeg_bin, run_media
                    probe = run_media([ffmpeg_bin('ffprobe'), '-v', 'error', '-select_streams', 'v:0',
                                       '-show_entries', 'stream=codec_type', '-of', 'csv=p=0', temporary])
                    if probe.returncode or probe.stdout.strip() != b'video':
                        raise ElysiumVideoError('Elysium вернул файл без видеопотока. ID сохранён, повторите скачивание.')
                    os.replace(temporary, out_path)
                    self.store.operation(operation, {**state, 'billing': metadata.get('billing')})
                    return 'elysium:' + self.model
                finally:
                    if os.path.exists(temporary):
                        os.remove(temporary)
            if progress:
                progress(f'{self.label} через Elysium: {status or "ожидание"}; задание {task}. Повторной отправки нет.')
            netutil.pause(self.poll)
        raise ElysiumVideoError('Elysium ещё готовит видео. ID сохранён: повторный запуск продолжит ожидание этого же задания.')
