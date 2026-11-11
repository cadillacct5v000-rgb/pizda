"""Bounded OpenAI-compatible SSE reader. Partial replies are never completions."""
import json
import time

import requests

import netutil
from model_json import text_content


class StreamError(requests.ConnectionError):
    pass


def read(response, progress=None, capture=None, *, deadline=900):
    started = time.monotonic()
    reported = started
    parts, data = [], []
    finish, refused, done = None, False, False
    count, total = 0, 0

    def result():
        return {'choices': [{'index': 0, 'finish_reason': finish,
                             'message': {'content': ''.join(parts), 'refusal': 'refused' if refused else None}}]}

    def dispatch():
        nonlocal finish, refused, done, count
        if not data:
            return
        payload = '\n'.join(data)
        data.clear()
        if payload.strip() == '[DONE]':
            done = True
            return
        try:
            item = json.loads(payload)
        except ValueError as error:
            raise StreamError('Сервис прислал повреждённое событие SSE.') from error
        if not isinstance(item, dict):
            raise StreamError('Неподдерживаемое событие SSE.')
        if item.get('error'):
            # Error payloads can echo keys: do not expose them as raw text.
            raise StreamError('Сервис сообщил ошибку внутри потока SSE.')
        choices = item.get('choices', [])
        if not isinstance(choices, list):
            raise StreamError('Некорректный список ответов SSE.')
        for choice in choices:
            if not isinstance(choice, dict) or choice.get('index', 0) != 0:
                continue
            delta = choice.get('delta') or choice.get('message') or {}
            if not isinstance(delta, dict):
                raise StreamError('Некорректный фрагмент ответа SSE.')
            if delta.get('content') is not None:
                try:
                    text = text_content(delta['content'])
                except ValueError as error:
                    raise StreamError('Неподдерживаемый текстовый фрагмент SSE.') from error
                # A second content update after finish would make the result ambiguous.
                if finish is not None and text:
                    raise StreamError('Сервис продолжил ответ после отметки завершения.')
                parts.append(text)
                count += len(text)
            refused = refused or bool(delta.get('refusal'))
            if choice.get('finish_reason') is not None:
                finish = choice['finish_reason']
                if finish not in ('stop', 'length', 'content_filter', 'tool_calls', 'function_call'):
                    raise StreamError('Сервис прислал неизвестную отметку завершения SSE.')

    if progress:
        progress('подключён поток ответа модели; ожидаю данные, запрос не повторяется')
    try:
        for raw_line in response.iter_lines(chunk_size=1):
            netutil.check_cancel()
            now = time.monotonic()
            if now - started > deadline:
                raise StreamError(f'Истёк общий лимит потока {deadline} с.')
            if now - reported >= 15:
                if progress:
                    progress(f'сервер на связи: {int(now - started)} с; получено {count} символов ответа')
                reported = now
            if isinstance(raw_line, bytes):
                total += len(raw_line)
                try:
                    line = raw_line.decode('utf-8-sig')
                except UnicodeError as error:
                    raise StreamError('Сервис прислал неверную кодировку SSE.') from error
            else:
                line = str(raw_line).lstrip('\ufeff')
                total += len(line.encode('utf-8'))
            if total > 8 * 1024 * 1024:
                raise StreamError('Поток ответа превысил допустимый размер 8 МиБ.')
            if not line:
                dispatch()
                if done or finish is not None:
                    # finish_reason proves completion; do not wait for a lingering
                    # proxy to close the connection or deliver optional usage/DONE.
                    return result()
            elif line.startswith('data:'):
                data.append(line[5:].removeprefix(' '))
            elif line.startswith(':'):
                continue  # Heartbeat keeps the read timeout alive, never becomes text.
            # event/id/retry fields carry no assistant content.
        dispatch()
        if done or finish is not None:
            return result()
        raise StreamError(f'Поток закрыт без отметки завершения; получено {count} символов.')
    except BaseException:
        if capture and parts:
            capture(''.join(parts), 'stream_interrupted', False)
        raise
