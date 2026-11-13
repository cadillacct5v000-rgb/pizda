"""Classify transport responses without exposing bodies or credentials."""
import re


class APIResponseError(RuntimeError):
    pass


def invalid_response(response, service, path, key=''):
    raw = getattr(response, 'text', '')
    text = raw if isinstance(raw, str) else ''
    status = getattr(response, 'status_code', '?')
    headers = getattr(response, 'headers', {}) or {}
    content_type = str(headers.get('Content-Type', '')).split(';')[0].strip()
    if not re.fullmatch(r'[A-Za-z0-9.+_-]+/[A-Za-z0-9.+_-]+', content_type):
        content_type = 'не указан'
    kind = ('перенаправление' if isinstance(status, int) and 300 <= status < 400 else
            'пустой ответ' if not text.strip() else 'HTML вместо JSON' if text.lstrip().startswith('<') else 'ответ не в формате JSON-объекта')
    detail = f'{service}: HTTP {status} · {path} · {kind} · тип {content_type}'
    for name in ('x-request-id', 'request-id', 'cf-ray'):
        value = headers.get(name)
        if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,90}', value) and (not key or key not in value):
            detail += ' · ID ' + value
            break
    return detail + '. Проверьте соединение с API и VPN/прокси. Принятие ключа по этому ответу не подтверждено.'


def json_object(response, service, path, key=''):
    status = getattr(response, 'status_code', 0)
    if isinstance(status, int) and 300 <= status < 400:
        raise APIResponseError(invalid_response(response, service, path, key))
    try:
        result = response.json()
    except ValueError as error:
        raise APIResponseError(invalid_response(response, service, path, key)) from error
    if not isinstance(result, dict):
        raise APIResponseError(invalid_response(response, service, path, key))
    return result
