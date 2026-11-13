"""Read-only model discovery and safe, actionable provider errors."""
from urllib.parse import urlsplit
import api_response

import requests

import endpoints
import quota_info

LABELS = {'elysium': 'Elysium', 'openai': 'OpenAI', 'anthropic': 'Claude', 'gemini': 'Gemini'}


def location(engine, url=None):
    parsed = urlsplit(url or endpoints.base(engine))
    # Credentials/query strings in custom endpoints must never appear in diagnostics.
    return (parsed.hostname or 'сервер') + parsed.path


def failure(engine, response, key='', model='', url=None):
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    error = payload.get('error', payload) if isinstance(payload, dict) else {}
    error = error if isinstance(error, dict) else {'message': str(error)}
    code = str(error.get('code') or error.get('type') or '')
    detail = quota_info.clean(error.get('message') or getattr(response, 'text', ''), (key,), 650)
    status = response.status_code
    context = f"{LABELS.get(engine, engine)} · {location(engine, url)}"
    if model:
        context += ' · ' + quota_info.clean(model, (key,), 100)
    message = f'{context}: HTTP {status}. {detail}'
    lower = (code + ' ' + detail).lower()
    if status == 401 or 'api_key_invalid' in lower or 'api key not valid' in lower:
        message += ' Сервер не принял ключ; это не ошибка баланса. Проверьте, кем выдан ключ и адрес API.'
        if engine == 'gemini' and not endpoints.is_custom(engine) and key.startswith('sk-'):
            message += ' В прямой Gemini API отправлен ключ sk-; ключ Google AI Studio обычно начинается с AIza. Ключ посредника требует его адреса и протокола API.'
    elif status == 403:
        message += ' Запрос запрещён сервером: проверьте права ключа, доступ к модели и ограничения аккаунта/региона. Смена ключа сама по себе может не помочь.'
    elif status == 404:
        message += ' Проверьте адрес API и идентификатор модели через «Модели с сервера».'
    elif status == 429 and any(word in lower for word in ('insufficient_quota', 'credit', 'billing_hard_limit')):
        message += ' Проверьте API-баланс и лимит расходов именно у сервиса, выдавшего этот ключ. Автоматический повтор остановлен.'
    return message


def exhausted(response):
    """A billing refusal is permanent for this attempt, unlike a transient 429."""
    try:
        error = response.json().get('error', {})
    except (ValueError, AttributeError):
        return False
    if not isinstance(error, dict):
        return False
    text = ' '.join(str(error.get(k) or '') for k in ('code', 'type', 'message')).lower()
    return response.status_code == 429 and any(v in text for v in ('insufficient_quota', 'credit', 'billing_hard_limit'))


def models(engine, key):
    if engine not in LABELS:
        raise ValueError('Выберите OpenAI, Claude или Gemini.')
    if not key:
        raise ValueError('Ключ не введён.')
    headers = {'x-goog-api-key': key} if engine == 'gemini' else (
        {'x-api-key': key, 'anthropic-version': '2023-06-01'} if engine == 'anthropic' else {'Authorization': 'Bearer ' + key})
    result, page = [], None
    for _ in range(20):
        params = {'pageSize': 1000} if engine == 'gemini' else {'limit': 100} if engine == 'anthropic' else {}
        if page:
            params['pageToken' if engine == 'gemini' else 'after_id'] = page
        with requests.get(endpoints.base(engine) + '/models', headers=headers, params=params, timeout=30) as response:
            if response.status_code >= 400:
                raise ValueError(failure(engine, response, key))
            data = api_response.json_object(response, LABELS.get(engine, engine), '/models', key)
        for item in data.get('models' if engine == 'gemini' else 'data', []):
            if not isinstance(item, dict):
                continue
            if engine == 'gemini' and 'generateContent' not in item.get('supportedGenerationMethods', []):
                continue
            identifier = item.get('name' if engine == 'gemini' else 'id')
            if isinstance(identifier, str) and identifier:
                result.append(identifier.removeprefix('models/') if engine == 'gemini' else identifier)
        next_page = data.get('nextPageToken') if engine == 'gemini' else data.get('last_id') if data.get('has_more') else None
        if not next_page:
            return sorted(set(result))
        if next_page == page:
            break
        page = next_page
    raise ValueError('Сервер не завершил выдачу списка моделей; неполный список не применён.')
