"""SYNTX desktop adapter. Billable requests use persisted dedicated chat IDs."""
import hashlib
import json
import os
import time
import threading
from pathlib import Path
import requests
import netutil
from fastgen_api import data_dir
from native_projects import ProjectStore


class SyntxError(RuntimeError):
    pass


class Syntx:
    def __init__(self, key, *, store=None, session=None, poll=5, timeout=1800):
        self.session = session or requests.Session()
        self.session.headers['Authorization'] = 'Bearer ' + key.removeprefix('Bearer ').strip()
        self.store = store or ProjectStore(data_dir())
        self.poll, self.timeout = poll, timeout
        self.lock = threading.Lock()

    def request(self, method, path, **kwargs):
        netutil.check_cancel()
        response = self.session.request(method, 'https://api.syntx.ai/api/v1/' + path.lstrip('/'), timeout=60, **kwargs)
        if response.status_code >= 400:
            # The provider can echo keys or narration in its raw error body.
            raise SyntxError(f'SYNTX HTTP {response.status_code}: проверьте ключ, баланс и доступ модели')
        return response.json()

    def balance(self):
        return self.request('GET', 'user/balance')

    def models(self):
        return self.request('GET', 'ai/models')

    def upload(self, path, model):
        with open(path, 'rb') as source:
            result = self.request('POST', 'chats/upload-files', files={'files': (Path(path).name, source, 'image/png')},
                                  data={'destination': 'hidden', 'check_duplicates': 'true', 'model_type': model})
        files = result.get('files') or (result.get('data') or {}).get('files') or []
        if not files or not files[0].get('url'):
            raise SyntxError('SYNTX не вернул ссылку на загруженный референс')
        return files[0]['url']

    def generate(self, prompt, target, *, kind='image', model=None, image=None, references=None, duration=5):
        model = model or ('gpt-image-2' if kind == 'image' else 'wan_26_i2v')
        sources = ([image] if image else []) + list(references or [])
        signature = {'kind': kind, 'prompt': prompt, 'model': model, 'duration': duration,
                     'target': str(Path(target).resolve()),
                     'sources': [hashlib.sha256(Path(file).read_bytes()).hexdigest() for file in sources]}
        key = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
        # The account key is hashed so tasks never leak between accounts and keys are never stored in SQLite.
        account = hashlib.sha256(self.session.headers['Authorization'].encode()).hexdigest()[:16]
        key = f'syntx:{account}:{key}'
        with self.lock:
            operation = self.store.operation(key)
            if operation and operation.get('status') == 'uncertain':
                raise SyntxError('SYNTX: ответ платной операции потерян. Проверьте кабинет; автоматический повтор заблокирован.')
            if not operation:
                chat = self.request('POST', 'chats', json={'title': 'KadroGen scene', 'scope': kind})
                if not chat.get('id') or not chat.get('uuid'):
                    raise SyntxError('SYNTX не вернул ID чата')
                operation = {'chat_id': chat['id'], 'chat_uuid': chat['uuid'], 'status': 'preparing'}
                self.store.operation(key, operation)
            if operation['status'] == 'preparing':
                urls = [self.upload(file, model) for file in sources[:3]]
                settings = {'model_type': model, 'aspect_ratio': '16:9'}
                if kind == 'image':
                    settings.update(n=1, image_url=urls, quality='medium', image_size='1K')
                    route, body = 'design/generate?ai_name=sora-images', {'chat_uuid': operation['chat_uuid'], 'prompt': prompt, 'settings': settings}
                else:
                    settings.update(file_urls=urls, resolution='1080p', video_duration=max(5, min(10, int(duration))))
                    route, body = 'video/generate?ai_name=wan_video', {'chat_id': operation['chat_id'], 'prompt': prompt, 'settings': settings}
                operation['status'] = 'sending'
                self.store.operation(key, operation)
                try:
                    accepted = self.request('POST', route, json=body)
                    operation.update(status='running', message_id=accepted.get('id'))
                except BaseException:
                    operation['status'] = 'uncertain'
                    self.store.operation(key, operation)
                    raise
                self.store.operation(key, operation)
            elif operation['status'] == 'sending':
                # A previous process died between sending and receiving. Poll the dedicated chat, never resend.
                operation['status'] = 'running'
                self.store.operation(key, operation)
        started = time.monotonic()
        try:
            while time.monotonic() - started < self.timeout:
                netutil.check_cancel()
                response = netutil.patient(lambda: self.request('GET', f"chats/{operation['chat_id']}/messages?page=1&page_size=50"))
                messages = response.get('messages') or (response.get('data') or {}).get('messages') or []
                objects = [obj for message in messages if message.get('author_id') == -1 for obj in message.get('message_object') or []]
                if any(obj.get('error') for obj in objects):
                    raise SyntxError('SYNTX: генерация отклонена. Измените описание или проверьте кабинет.')
                result = next((obj for obj in objects if obj.get('completed') and obj.get('object_type') in ('image', 'image_url', 'video') and obj.get('object_url')), None)
                if result:
                    url = result['object_url']
                    if not isinstance(url, str) or not url.startswith('https://'):
                        raise SyntxError('SYNTX вернул небезопасную ссылку')
                    # Fresh requests session: never pass the SYNTX Authorization header to its CDN.
                    netutil.save_response(requests.get(url, stream=True, timeout=300), target)
                    operation['status'] = 'ready'
                    self.store.operation(key, operation)
                    return 'syntx:' + model
                netutil.pause(self.poll)
            raise SyntxError('SYNTX: время ожидания истекло. ID сохранён; следующий запуск продолжит эту же задачу.')
        except BaseException as error:
            if type(error).__name__ == 'Cancelled' and operation.get('message_id'):
                try:
                    # Cancellation has its own bounded request; the local cancellation flag must not suppress it.
                    self.session.post(f"https://api.syntx.ai/api/v1/chats/{operation['chat_id']}/messages/{operation['message_id']}/cancel", timeout=15)
                except requests.RequestException:
                    pass  # No refund or remote cancellation is promised; the saved chat remains resumable.
            raise


class SyntxGate:
    def __init__(self, api):
        self.api = api
        self.threads = 1
        self.sem = threading.Semaphore(1)
        self.vsem = threading.Semaphore(2)
