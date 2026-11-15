"""Read-only model discovery and safe, actionable provider errors."""
from urllib.parse import urlsplit
import re
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


class ModelIds(list):
    def __init__(self,values=(),metadata=None):
        super().__init__(values);self.metadata=metadata or {}


def models(engine, key):
    if engine not in LABELS:
        raise ValueError('Выберите OpenAI, Claude или Gemini.')
    if not key:
        raise ValueError('Ключ не введён.')
    headers = {'x-goog-api-key': key} if engine == 'gemini' else (
        {'x-api-key': key, 'anthropic-version': '2023-06-01'} if engine == 'anthropic' else {'Authorization': 'Bearer ' + key})
    result, page, metadata = [], None, {}
    for _ in range(20):
        params = {'pageSize': 1000} if engine == 'gemini' else {'limit': 100} if engine == 'anthropic' else {}
        if page:
            params['pageToken' if engine == 'gemini' else 'after_id'] = page
        with requests.get(endpoints.base(engine) + '/models', headers=headers, params=params, timeout=30) as response:
            if response.status_code >= 400:
                raise ValueError(failure(engine, response, key))
            data = api_response.json_object(response, LABELS.get(engine, engine), '/models', key)
        rows = data.get('models' if engine == 'gemini' else 'data')
        if not isinstance(rows, list) or any(not isinstance(item, dict) for item in rows):
            raise ValueError('Сервер не вернул корректный список моделей; настройки не изменены.')
        for item in rows:
            if not isinstance(item, dict):
                continue
            if engine == 'gemini' and 'generateContent' not in item.get('supportedGenerationMethods', []):
                continue
            identifier = item.get('name' if engine == 'gemini' else 'id')
            if isinstance(identifier, str) and identifier:
                identifier=identifier.removeprefix('models/') if engine=='gemini' else identifier
                result.append(identifier);metadata[identifier]=item
        next_page = data.get('nextPageToken') if engine == 'gemini' else data.get('last_id') if data.get('has_more') else None
        if not next_page:
            return ModelIds(sorted(set(result)),metadata)
        if next_page == page:
            break
        page = next_page
    raise ValueError('Сервер не завершил выдачу списка моделей; неполный список не применён.')


def sonnet_model(engine, key, requested=None, *, available=None):
    """Choose an exact advertised Sonnet ID; never invent a provider route."""
    if engine not in ('elysium', 'anthropic'):
        raise ValueError('Выберите Elysium или Anthropic.')
    prefix = 'anthropic/' if engine == 'elysium' else ''
    pattern = re.compile(re.escape(prefix) + r'claude-sonnet-(\d+)(?:[.-](\d{1,2})(?=$|[-.]))?(.*)$')
    if available is None:available = models(engine, key)
    candidates = []
    for identifier in available:
        match = pattern.fullmatch(identifier)
        if match:
            major, minor, suffix = match.groups()
            candidates.append((int(major), int(minor or 0), not bool(suffix), identifier))
    if requested:
        if not pattern.fullmatch(requested) or requested not in available:
            raise ValueError('Выбранный Claude Sonnet отсутствует в каталоге этого ключа. Настройки не изменены.')
        return requested
    if not candidates:
        raise ValueError('В каталоге этого ключа нет Claude Sonnet. Настройки не изменены.')
    return max(candidates)[-1]

# Chat endpoints must not receive media-generation, transcription or embedding models.
_MEDIA_ID=re.compile(r'(?:seedream|kling|hailuo|(?:^|[/_-])wan(?:[./_-]|$)|veo(?:[./_-]|$)|sora(?:[./_-]|$)|imagen|flux|dall-e|ideogram|recraft|stable-diffusion|midjourney|(?:gpt|chatgpt|qwen)[^/]*image|nano[-_]banana|elevenlabs|whisper|embed|moderation|realtime|transcribe|(?:gpt|gemini|qwen)[^/]*audio|text-to-speech|(?:^|[/_-])tts(?:[./_-]|$)|gemini[^/]*(?:image|tts|video)|grok[^/]*(?:image|video))',re.I)

def supports_task_model(identifier, purpose='text',metadata=None):
    """Local endpoint compatibility, not proof of account access or generation quality."""
    if purpose not in ('text','vision'):raise ValueError('Неизвестное назначение модели.')
    name=str(identifier).lower()
    if _MEDIA_ID.search(name):return False
    if isinstance(metadata,dict):
        kind=metadata.get('kind') or metadata.get('type')
        if kind in ('image','video','audio','embedding','image_generation','video_generation','text-to-speech'):return False
        inputs=metadata.get('input_modalities')
        outputs=metadata.get('output_modalities')
        if isinstance(outputs,list) and outputs and 'text' not in outputs:return False
        if isinstance(inputs,list) and isinstance(outputs,list) and 'text' in outputs:
            return 'image' in inputs if purpose=='vision' else True
        capabilities=metadata.get('capabilities')
        if isinstance(capabilities,dict):
            if purpose=='vision' and type(capabilities.get('vision')) is bool:return capabilities['vision']
            if purpose=='text' and type(capabilities.get('chat')) is bool:return capabilities['chat']
    if purpose=='text':
        return True if re.search(r'(?:^|/)(?:claude-|gemini-|gpt-|chatgpt-|o[134](?:-|$)|grok-|qwen|deepseek|llama|mistral|command)',name) else None
    if re.search(r'(?:^|/)(?:claude-|gemini-|gpt-4o|gpt-4\.1|gpt-5|o[34](?:-|$))',name) or re.search(r'(?:vision|qwen.*vl|llama.*vision)',name):return True
    if re.search(r'(?:gpt-3\.5|gpt-4(?:-|$)|deepseek.*(?:chat|reasoner)|mistral)',name):return False
    return None


def filter_models(values,purpose='text'):
    # Unknown capabilities are not advertised as suitable in a task-specific picker.
    return [name for name in values if supports_task_model(name,purpose,getattr(values,'metadata',{}).get(name)) is True]


def require_task_model(identifier,purpose='text'):
    if supports_task_model(identifier,purpose) is False:
        task='проверки изображений' if purpose=='vision' else 'работы с текстом'
        raise ValueError(f'Модель {identifier} не подходит для {task}: нужен чат API'+(' с поддержкой изображений.' if purpose=='vision' else '.')+' Выберите модель через «Модели с сервера» в этом разделе.')
