"""Elysium's asynchronous media API. Never retry a billable submission."""
import hashlib
import json
import math
import os
import time
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
import netutil
from fastgen_api import data_dir
from native_projects import ProjectStore

MODEL = 'google/gemini-omni-flash-1.2'
DEFAULT_BASE = 'https://elysiumai.garden/v1'


class ElysiumVideoError(RuntimeError):
    pass


class ElysiumVideo:
    def __init__(self, key, base=None, resolution='720p', *, store=None, session=None, poll=5, max_wait=1800):
        if not key:
            raise ElysiumVideoError('Введите ключ Elysium для оживления Gemini.')
        self.base = (base or DEFAULT_BASE).rstrip('/')
        if urlparse(self.base).scheme != 'https':
            raise ElysiumVideoError('Для Elysium Video нужен адрес HTTPS.')
        if resolution not in ('720p', '1080p'):
            raise ElysiumVideoError('Выберите 720p или 1080p.')
        self.resolution = resolution
        self.session = session or requests.Session()
        self.session.headers['Authorization'] = 'Bearer ' + key
        self.account = hashlib.sha256(key.encode()).hexdigest()
        self.store = store or ProjectStore(data_dir())
        self.poll, self.max_wait = poll, max_wait

    def _json(self, response):
        if response.status_code >= 400:
            # Remote errors can echo the API key or signed reference URL.
            raise ElysiumVideoError(f'Elysium Video HTTP {response.status_code}: проверьте баланс, права ключа и модель {MODEL}.')
        result = response.json()
        if not isinstance(result, dict):
            raise ElysiumVideoError('Elysium Video вернул некорректный ответ.')
        return result

    def _get(self, path):
        return self._json(netutil.patient(lambda: self.session.get(self.base + path, timeout=60)))

    def check(self):
        # Authenticated GET; no generation or billing. Catalog presence is not a paid access test.
        self._get('/models')
        catalog = self._get('/media/models')
        model = next((m for m in catalog.get('data', []) if m.get('id') == MODEL), None)
        if not model or not any(o.get('name') == 'generate' for o in model.get('operations', [])):
            raise ElysiumVideoError(f'В медиакаталоге Elysium нет {MODEL}.')
        return f'Ключ принят; {MODEL} есть в каталоге. Генерация не запускалась; права и списание проверяются при запуске клипа.'

    def generate(self, prompt, image, out_path, duration, progress=None):
        seconds = max(3, min(15, math.ceil(float(duration))))
        image_hash = hashlib.sha256(Path(image).read_bytes()).hexdigest()
        identity = [self.base, self.account, MODEL, self.resolution, seconds, prompt, image_hash]
        operation = 'elysium-video:' + hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        state = self.store.operation(operation) or {}
        if state.get('status') == 'failed':
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
            body = {'model': MODEL, 'operation': 'generate', 'prompt': prompt,
                    'input': {'prompt': prompt, 'duration': seconds, 'resolution': self.resolution,
                              'aspect_ratio': '16:9', 'first_frame_url': url}}
            netutil.check_cancel()
            self.store.operation(operation, {'status': 'submitting'})
            try:
                response = self.session.post(self.base + '/videos', json=body, timeout=120)
                if 400 <= response.status_code < 500:
                    self.store.operation(operation, {'status': 'rejected'})
                    self._json(response)
                result = self._json(response)
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
            result = self._get('/videos/' + quote(task, safe=''))
            status = result.get('status')
            state = {'id': task, 'status': status or 'unknown'}
            if status == 'failed':
                self.store.operation(operation, state)
                raise ElysiumVideoError('Elysium не создал клип. Подробности — в истории сервиса. Новое платное задание автоматически не отправляется.')
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
                    return 'elysium:' + MODEL
                finally:
                    if os.path.exists(temporary):
                        os.remove(temporary)
            if progress:
                progress(f'Gemini через Elysium: {status or "ожидание"}; задание {task}. Повторной отправки нет.')
            netutil.pause(self.poll)
        raise ElysiumVideoError('Elysium ещё готовит видео. ID сохранён: повторный запуск продолжит ожидание этого же задания.')
