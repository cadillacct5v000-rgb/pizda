"""Temporary public image references. Never use the video provider's HTTP session."""
import hashlib
import io
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image
import requests

import netutil

UPLOAD_ENDPOINT = 'https://uguu.se/upload'
LIFETIME = 3 * 60 * 60
MAX_BYTES = 20 * 1024 * 1024


class PublicImageError(RuntimeError):
    pass


def checked_image(path):
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise PublicImageError('Кадр больше 20 МБ. Видеозадание не отправлено.')
    data = path.read_bytes()
    try:
        with Image.open(io.BytesIO(data)) as source:
            kind = source.format
            source.verify()
        mime, extension = {'PNG': ('image/png', 'png'), 'JPEG': ('image/jpeg', 'jpg'),
                           'WEBP': ('image/webp', 'webp')}[kind]
    except Exception:
        raise PublicImageError('Кадр должен быть исправным PNG, JPEG или WebP. Видеозадание не отправлено.') from None
    return data, mime, extension


def valid_url(url):
    if not isinstance(url, str):
        return False
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == 'https' and parsed.hostname is not None
                and (parsed.hostname == 'uguu.se' or parsed.hostname.endswith('.uguu.se'))
                and parsed.username is None and parsed.password is None
                and parsed.port in (None, 443) and not parsed.query and not parsed.fragment
                and re.fullmatch(r'/[A-Za-z0-9_-]+\.(png|jpg|jpeg|webp)', parsed.path, re.I) is not None)
    except ValueError:
        return False


class PublicImages:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _verify(url, fingerprint, size):
        # No Authorization header or Elysium session; download must be publicly accessible.
        netutil.check_cancel()
        with requests.get(url, stream=True, timeout=(15, 60), allow_redirects=False) as result:
            if result.status_code != 200:
                raise PublicImageError(f'Хранилище не отдало кадр (HTTP {result.status_code}). Видеозадание не отправлено.')
            digest = hashlib.sha256()
            received = 0
            for part in result.iter_content(65536):
                netutil.check_cancel()
                received += len(part)
                if received > size:
                    raise PublicImageError('Публичный файл не совпадает с кадром. Видеозадание не отправлено.')
                digest.update(part)
            if received != size or digest.hexdigest() != fingerprint:
                raise PublicImageError('Публичный файл не совпадает с кадром. Видеозадание не отправлено.')

    def ensure(self, image, progress=None):
        data, mime, extension = checked_image(image)
        fingerprint = hashlib.sha256(data).hexdigest()
        operation = 'public-image:uguu:' + fingerprint
        saved = self.store.operation(operation) or {}
        now = time.time()
        # Upload just before generation. A fresh job should have at least one hour of URL life.
        if valid_url(saved.get('url')) and saved.get('expires_at', 0) > now + 3600:
            if progress:
                progress('Проверяю сохранённую публичную ссылку на кадр; новое видеозадание пока не отправляется.')
            try:
                self._verify(saved['url'], fingerprint, len(data))
                return saved['url']
            except (requests.RequestException, PublicImageError):
                pass  # Uploading the PNG again cannot create a second paid video.
        netutil.check_cancel()
        if progress:
            progress('Загружаю готовый кадр в Uguu (временная публичная ссылка на 3 часа); видео пока не отправляется.')
        # No Elysium key, project path or script text in this request. No automatic upload retries.
        try:
            with requests.post(UPLOAD_ENDPOINT, files={'files[]': ('frame.' + extension, data, mime)},
                               timeout=(15, 120), allow_redirects=False) as result:
                if result.status_code != 200:
                    raise PublicImageError(f'Uguu: HTTP {result.status_code} при загрузке кадра. Видеозадание не отправлено.')
                body = result.json()
            files = body.get('files', []) if isinstance(body, dict) else []
            if not body.get('success') or not isinstance(files, list) or not files:
                raise PublicImageError('Uguu не подтвердил загрузку кадра. Видеозадание не отправлено.')
            url = files[0].get('url') if isinstance(files[0], dict) else None
            if not valid_url(url):
                raise PublicImageError('Хранилище вернуло некорректную HTTPS-ссылку. Видеозадание не отправлено.')
            self._verify(url, fingerprint, len(data))
        except requests.RequestException:
            raise PublicImageError('Не удалось загрузить или проверить кадр в Uguu. Видеозадание не отправлено; повторите позже.') from None
        except (ValueError, TypeError, AttributeError):
            raise PublicImageError('Хранилище вернуло некорректный ответ. Видеозадание не отправлено.') from None
        # Retain only after a public GET matched the local bytes. Never store source filenames.
        self.store.operation(operation, {'status': 'ready', 'url': url, 'sha256': fingerprint,
                                         'uploaded_at': now, 'expires_at': now + LIFETIME})
        return url
