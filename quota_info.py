"""Normalize quota errors without exposing credentials or assuming quota resets."""
import math
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def clean(value, secrets=(), maximum=500):
    result = str(value or '')
    for secret in secrets:
        if isinstance(secret, str) and len(secret) >= 4:
            result = result.replace(secret, '[скрыто]')
    result = re.sub(r'AIza[A-Za-z0-9_-]+|sk-[A-Za-z0-9_-]+', '[ключ скрыт]', result)
    result = re.sub(r'https?://[^\s\"\']+', '[URL]', result)
    return result[:maximum]


def describe(payload, model='', secrets=(), retry_after=None):
    error = payload.get('error', payload) if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        error = {}
    violations, retry = [], None
    for detail in error.get('details') or []:
        if not isinstance(detail, dict):
            continue
        for violation in detail.get('violations') or []:
            if not isinstance(violation, dict):
                continue
            identifier = clean(violation.get('quotaId') or violation.get('quotaMetric'), secrets, 180)
            raw_limit = violation.get('quotaValue')
            value = str(raw_limit) if isinstance(raw_limit, (int, float, str)) else ''
            if value and not re.fullmatch(r'\d+(?:\.\d+)?', value):
                value = ''
            dimensions = violation.get('quotaDimensions') or {}
            if not model and isinstance(dimensions, dict):
                model = dimensions.get('model') or ''
            violations.append({'id': identifier, 'limit': value})
        delay = detail.get('retryDelay')
        if isinstance(delay, str) and re.fullmatch(r'\d+(?:\.\d+)?s', delay):
            retry = max(retry or 0, math.ceil(float(delay[:-1])))
    if retry_after is not None:
        retry = max(retry or 0, retry_after)
    identifiers = ' '.join(item['id'] for item in violations).lower()
    normalized = re.sub(r'[^a-z]', '', identifiers)
    if any(item['limit'] and float(item['limit']) == 0 for item in violations):
        kind, explanation = 'zero', 'Сервис сообщил квоту 0 для этой модели/проекта. Ожидание может не помочь: проверьте доступную модель и квоту проекта.'
    elif 'perday' in normalized or 'daily' in normalized:
        kind, explanation = 'daily', 'Исчерпана суточная квота. Повторные запуски не восстановят её; дождитесь сброса, указанного в кабинете сервиса.'
    elif 'perminute' in normalized or 'rpm' in normalized or 'tpm' in normalized:
        kind, explanation = 'minute', 'Превышен минутный лимит запросов или токенов.'
    else:
        kind, explanation = 'unknown', 'Квота запросов или токенов превышена; период ограничения автоматически определить не удалось.'
    label = clean(model, secrets, 100)
    message = 'HTTP 429' + (f' · {label}' if label else '') + ': ' + explanation
    limits = [item['id'] + (f"={item['limit']}" if item['limit'] else '') for item in violations[:2] if item['id']]
    if limits:
        message += ' Лимит: ' + '; '.join(limits) + '.'
    if not violations:
        detail = clean(error.get('message'), secrets, 260)
        if detail:
            message += ' ' + detail
    if retry is not None and kind not in ('daily', 'zero'):
        message += f' Сервис предлагает повтор через {retry} с.'
    return {'kind': kind, 'retry_seconds': retry, 'message': message, 'violations': violations}


def from_response(response, model='', secrets=()):
    try:
        payload = response.json()
    except (ValueError, TypeError):
        payload = {}
    header = (getattr(response, 'headers', None) or {}).get('Retry-After')
    retry = None
    if isinstance(header, str):
        try:
            value = float(header)
        except ValueError:
            try:
                value = (parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                value = None
        if value is not None and math.isfinite(value):
            retry = max(0, math.ceil(value))
    return describe(payload, model, secrets, retry)
