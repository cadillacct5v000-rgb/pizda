"""Conservative model JSON parsing: preserve values, never complete missing data."""
import json
import re
import ast


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


def recover_object(content):
    """Repair separators/quoting only; never insert values or closing brackets.

    Kept separate from the strict parser so callers must opt in. All keys and
    values must already be present. Ambiguous strings remain a model repair.
    """
    try:
        return extract_object(content)
    except (ValueError, TypeError):
        pass
    text = text_content(content).strip().lstrip('\ufeff').strip()
    fences = re.findall(r'```(?:json)?[ \t]*\r?\n(.*?)```', text, re.S | re.I)
    if len(fences) > 1:
        raise json.JSONDecodeError('Несколько JSON-блоков', text, 0)
    if fences:
        text = fences[0].strip()
    elif text.startswith('```'):
        match = re.fullmatch(r'```(?:json)?\s*(.*?)\s*```', text, re.S | re.I)
        if match:
            text = match.group(1).strip()
    if text.startswith('[') or '{' not in text or len(text) > 100000:
        raise json.JSONDecodeError('Нет однозначного объекта JSON', text, 0)
    text = text[text.find('{'):]
    pos = 0
    def fail(message):
        raise json.JSONDecodeError(message, text, pos)
    def whitespace():
        nonlocal pos
        while pos < len(text) and text[pos].isspace():
            pos += 1
    def invalid_constant(value):
        fail('Недопустимая константа')
    decoder = json.JSONDecoder(strict=False, parse_constant=invalid_constant)
    def string():
        nonlocal pos
        quote, start = text[pos], pos
        pos += 1
        token = [quote]
        while pos < len(text):
            char = text[pos];pos += 1
            token.append(char)
            if char == quote:
                try:
                    value = ast.literal_eval(''.join(token)) if quote == "'" else decoder.decode(''.join(token))
                except (ValueError, SyntaxError):
                    fail('Некорректная строка')
                if not isinstance(value, str):
                    fail('Ожидалась строка')
                return value
            if char == '\\':
                if pos >= len(text):
                    fail('Обрезанная строка')
                escaped = text[pos];pos += 1
                if quote == '"' and escaped not in '"\\/bfnrtu':
                    # An invalid JSON escape such as \_ represents a literal
                    # backslash, not permission to silently drop that character.
                    token.append('\\')
                token.append(escaped)
        pos = start
        fail('Обрезанная строка')
    def value(depth=0):
        nonlocal pos
        whitespace()
        if depth > 64 or pos >= len(text):
            fail('Обрезанное или слишком глубокое значение')
        char = text[pos]
        if char in ('"', "'"):
            return string()
        if char in '{[':
            object_mode = char == '{'
            closing = '}' if object_mode else ']'
            result = {} if object_mode else []
            pos += 1;whitespace()
            if pos < len(text) and text[pos] == closing:
                pos += 1;return result
            while True:
                whitespace()
                if object_mode:
                    if pos >= len(text) or text[pos] not in ('"', "'"):
                        fail('Ожидался ключ объекта')
                    key = string();whitespace()
                    if key in result or pos >= len(text):
                        fail('Повторный ключ или отсутствует значение')
                    if text[pos] == ':':
                        pos += 1
                    elif text[pos] not in '{["\'-0123456789tfn':
                        fail('Отсутствует однозначное значение после ключа')
                    result[key] = value(depth+1)
                else:
                    result.append(value(depth+1))
                whitespace()
                if pos >= len(text):
                    fail('Обрезанный контейнер')
                if text[pos] == closing:
                    pos += 1;return result
                if text[pos] == ',':
                    pos += 1;whitespace()
                    if pos < len(text) and text[pos] == closing:
                        pos += 1;return result
                # With a missing comma, the next loop must still parse a full
                # key/value or array item. Nothing is synthesized to fill gaps.
                elif text[pos] not in ('"\'' if object_mode else '{["\'-0123456789tfn'):
                    fail('Неоднозначный разделитель')
        try:
            result, end = decoder.raw_decode(text, pos)
        except ValueError:
            fail('Некорректное значение')
        if isinstance(result, (dict, list, str)):
            fail('Неоднозначное значение')
        pos = end
        return result
    result = value()
    if not isinstance(result, dict) or any(char in text[pos:] for char in '{}[]'):
        fail('Неоднозначный объект JSON')
    return result
