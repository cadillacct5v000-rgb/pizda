"""Conservative model JSON parsing: preserve values, never complete missing data."""
import json
import re


def text_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict) or block.get('type') not in ('text', 'output_text') or not isinstance(block.get('text'), str):
                raise ValueError('Unsupported response content block')
            parts.append(block['text'])
        return ''.join(parts)
    raise ValueError('Response content is not text')


def _remove_trailing_commas(text):
    result, quoted, escaped = [], False, False
    for index, char in enumerate(text):
        if quoted:
            result.append(char)
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        else:
            if char == '"':
                quoted = True
            if char == ',':
                following = text[index + 1:].lstrip()
                if following.startswith(('}', ']')):
                    continue
            result.append(char)
    return ''.join(result)


def extract_object(content):
    text = text_content(content).strip().lstrip('\ufeff').strip()
    # A complete fenced JSON answer takes precedence over surrounding prose.
    fences = re.findall(r'```(?:json)?[ \t]*\r?\n(.*?)```', text, re.S | re.I)
    if len(fences) > 1:
        raise json.JSONDecodeError('Несколько JSON-блоков: неоднозначный ответ', text, 0)
    if fences:
        text = fences[0].strip()
    elif text.startswith('```'):
        # Also accept single-line JSON fences, without accepting unfinished ones.
        match = re.fullmatch(r'```(?:json)?\s*(.*?)\s*```', text, re.S | re.I)
        if match:
            text = match.group(1).strip()
    if text.startswith('['):
        raise json.JSONDecodeError('Ожидался объект, получен массив', text, 0)
    start = text.find('{')
    if start < 0:
        raise json.JSONDecodeError('Нет объекта JSON', text, 0)
    candidate = _remove_trailing_commas(text[start:])
    def invalid_constant(value):
        raise json.JSONDecodeError('Недопустимая константа ' + value, candidate, 0)
    # strict=False accepts literal newlines/tabs inside strings; values stay intact.
    result, end = json.JSONDecoder(strict=False, parse_constant=invalid_constant).raw_decode(candidate)
    if not isinstance(result, dict):
        raise json.JSONDecodeError('Ожидался объект JSON', candidate, 0)
    remainder = candidate[end:].strip()
    if remainder and any(char in remainder for char in '{}[]'):
        raise json.JSONDecodeError('Лишние JSON-данные после ответа', candidate, end)
    return result
