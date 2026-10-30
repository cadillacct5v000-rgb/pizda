# -*- coding: utf-8 -*-
"""Рерайт сценария: источник — ссылка на YouTube (субтитры ролика) или свой текст;
переписывает языковая модель по ключу пользователя (ChatGPT / Claude / Gemini) или через чат fast-gen."""
import os
import re
import json
import time
import hashlib
import subprocess

import requests
import netutil
import endpoints

PROMPT = """Прочитай и проанализируй весь сценарий видео на ютубе целиком и полностью.
Есть ли какие то ошибки в тексте, которые плохо повлияли бы на удержание и вовлечение зрителя?
Цель и задача рассказа: стопроцентное удержание зрителя интересным текстом, максимальная вовлеченность именно КАЧЕСТВОМ интересного сценария без фактологических ошибок. У СЕБЯ В ГОЛОВЕ АНАЛИЗИРУЙ, МНЕ НИЧЕГО НЕ ПИШИ.
исправим в тексте то что ты расписал, а также:
1 полностью уберем все призывы к подпискам, лайкам, комментариям и тому подобное (если имеется)
2 сделаем идеальное байтовое вступление (хук), которое заставит зрителя досмотреть до самого конца и приведёт к максимальному удержанию и вовлечению
Пиши на {language} языке сценарий txt-файлом, не md, без технического текста - сразу на озвучку, примерно на {words} слов, цифры и числа пиши буквами.
"Отрерайти" текст сразу с нужным объемом, напиши его как бы другими словами, особенно начало, нужен как бы новый, уникальный рассказ с исправленными ошибками
Текст объемный, имей ввиду СРАЗУ это при написании. Не меняй тему суть и нарратив рассказа
Цель и задача - максимальное удержание и вовлеченность зрителя на ютубе который будет слушать этот рассказ. ОТПРАВЬ МНЕ ИСКЛЮЧИТЕЛЬНО ТЕКСТ.
Начни ответ сразу с первого предложения сценария. Никаких вступлений вроде «Вот сценарий», «Принял», заголовков, названий, пометок и заметок в конце.

Сценарий:
{script}"""

# язык из списка программы (в предложном падеже, как в промпте) → (английское название, самоназвание, кириллица ли)
LANG_INFO = {
    "английском": ("English", "English", False), "китайском": ("Chinese", "中文", False), "хинди": ("Hindi", "हिन्दी", False),
    "испанском": ("Spanish", "español", False), "арабском": ("Arabic", "العربية", False), "французском": ("French", "français", False),
    "бенгальском": ("Bengali", "বাংলা", False), "португальском": ("Portuguese", "português", False), "русском": ("Russian", "русский", True),
    "урду": ("Urdu", "اردو", False), "индонезийском": ("Indonesian", "Bahasa Indonesia", False), "немецком": ("German", "Deutsch", False),
    "японском": ("Japanese", "日本語", False), "турецком": ("Turkish", "Türkçe", False), "вьетнамском": ("Vietnamese", "Tiếng Việt", False),
    "корейском": ("Korean", "한국어", False), "итальянском": ("Italian", "italiano", False), "персидском": ("Persian", "فارسی", False),
    "польском": ("Polish", "polski", False), "украинском": ("Ukrainian", "українська", True), "голландском": ("Dutch", "Nederlands", False),
    "румынском": ("Romanian", "română", False), "греческом": ("Greek", "ελληνικά", False), "чешском": ("Czech", "čeština", False),
    "венгерском": ("Hungarian", "magyar", False), "шведском": ("Swedish", "svenska", False), "казахском": ("Kazakh", "қазақша", True),
    "узбекском": ("Uzbek", "oʻzbekcha", False), "азербайджанском": ("Azerbaijani", "azərbaycanca", False), "иврите": ("Hebrew", "עברית", False),
    "финском": ("Finnish", "suomi", False), "датском": ("Danish", "dansk", False), "норвежском": ("Norwegian", "norsk", False),
}


def lang_rule(language):
    """Жёсткое указание языка — ставится В НАЧАЛО и В КОНЕЦ каждого запроса. Раньше язык был одним словом в середине русской
    инструкции, и на длинных сценариях модель срывалась на русский (тестер: половина по-русски, половина по-польски)."""
    en, native, _ = LANG_INFO.get(language, (language, language, None))
    return (f"OUTPUT LANGUAGE: {en} ({native}). Write the ENTIRE script ONLY in {en}. Every sentence must be in {en}, "
            f"even though these instructions and the source may be in another language. Do not switch languages anywhere.")


def wrong_language(text, language):
    """Грубая, но надёжная проверка под жалобы тестеров: русский текст вместо испанского/польского.
    Для языков не на кириллице доля кириллических букв должна быть мала; для кириллических — наоборот."""
    cyr = LANG_INFO.get(language, (None, None, None))[2]
    letters = [ch for ch in text if ch.isalpha()]
    if cyr is None or len(letters) < 80:
        return False
    share = sum(1 for ch in letters if "Ѐ" <= ch <= "ӿ") / len(letters)
    return share > 0.12 if not cyr else share < 0.5


def bad_paragraphs(text, language):
    """Абзацы не на том языке (модель могла сорваться посреди части). Возвращает (номера, абзацы)."""
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    return [i for i, p in enumerate(paras) if wrong_language(p, language)], paras


ENGINES = ("openai", "anthropic", "gemini", "fastgen")
ENGINE_NAMES = {"openai": "ChatGPT", "anthropic": "Claude", "gemini": "Gemini", "fastgen": "fast-gen"}
DEFAULT_MODELS = {"openai": "gpt-4.1", "anthropic": "claude-sonnet-5-5", "gemini": "gemini-3.6-flash", "fastgen": "openai/gpt-4.1-mini"}


class RewriteError(Exception):
    pass


class ModelUnavailable(RewriteError):
    pass


class IncompleteRewrite(RewriteError):
    """The provider exhausted its output limit before finishing the narration."""


def completion_text(response):
    choice = response["choices"][0]
    if choice.get("finish_reason") == "content_filter" or choice["message"].get("refusal"):
        raise RewriteError("Модель отклонила запрос по правилам сервиса; результат не принят.")
    if choice.get("finish_reason") == "length":
        raise IncompleteRewrite("Модель достигла лимита ответа. Обрезанный результат не принят; нужна меньшая часть сценария.")
    return choice["message"]["content"]


# ---------- YouTube ----------
from youtube_source import video_id, YouTubeError
from youtube_source import transcript as youtube_transcript
from youtube_source import audio as youtube_audio


def youtube_id(url):
    try:
        return video_id(url)
    except YouTubeError as exc:
        raise RewriteError(str(exc)) from exc


# ---------- языковые модели, обычный текст ----------
def _retry(fn, tries=4):
    last = None
    for attempt in range(tries):
        netutil.check_cancel()
        try:
            return fn()
        except requests.RequestException as e:
            last = str(e)
            if attempt < tries - 1:
                netutil.pause(5 * (attempt + 1))
        except RewriteError as e:
            if not e.args or not str(e).startswith("retry"):
                raise
            last = str(e)
            if attempt < tries - 1:
                netutil.pause(10 * (attempt + 1))
    raise ModelUnavailable(f"Сервис модели не ответил после {tries} попыток. Соединение могло оборваться на стороне сервиса или сети. Повторите рерайт; исходный текст сохранён. Причина: {last}")


def chat_text(engine, key, model, prompt, max_tokens):
    model = model or DEFAULT_MODELS[engine]
    if engine == "openai":
        def go():
            from openai_api import _chat_params
            r = requests.post(endpoints.base("openai") + "/chat/completions", headers={"Authorization": f"Bearer {key}"},
                              json={"model": model, **_chat_params(model, max_tokens, 0.8),
                                    "messages": [{"role": "user", "content": prompt}]}, timeout=600)
            if r.status_code == 429 or r.status_code >= 500:
                raise RewriteError(f"retry {r.status_code}: {r.text[:150]}")
            if r.status_code >= 400:
                raise RewriteError(f"ChatGPT {r.status_code}: {r.text[:200]}")
            return completion_text(r.json())
        return _retry(go)
    if engine == "anthropic":
        def go():
            r = requests.post(endpoints.base("anthropic") + "/messages",
                              headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                              json={"model": model, "max_tokens": max_tokens,
                                    "messages": [{"role": "user", "content": prompt}]}, timeout=600)
            if r.status_code in (429, 500, 502, 503, 529):
                raise RewriteError(f"retry {r.status_code}: {r.text[:150]}")
            if r.status_code >= 400:
                raise RewriteError(f"Claude {r.status_code}: {r.text[:200]}")
            payload = r.json()
            reason = payload.get("stop_reason")
            if reason == "max_tokens":
                raise IncompleteRewrite("Claude достиг лимита ответа. Обрезанный результат не принят; нужна меньшая часть сценария.")
            if reason and reason not in ("end_turn", "stop_sequence"):
                raise RewriteError(f"Claude не завершил рерайт (stop_reason={reason}); результат не принят.")
            return "".join(b.get("text", "") for b in payload.get("content", []) if b.get("type") in (None, "text"))
        return _retry(go)
    if engine == "gemini":
        limit = max_tokens + 4000  # у Gemini 2.5 «размышления» съедают лимит ответа — даём запас и режем бюджет размышлений

        def go():
            gen = {"temperature": 0.8, "maxOutputTokens": limit}
            if "2.5-flash" in model:
                gen["thinkingConfig"] = {"thinkingBudget": 0}
            elif "2.5-pro" in model:
                gen["thinkingConfig"] = {"thinkingBudget": 128}  # у pro размышления выключить нельзя, минимум 128
            r = requests.post(f"{endpoints.base('gemini')}/models/{model}:generateContent",
                              params={"key": key},
                              json={"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": gen}, timeout=600)
            if r.status_code in (429, 500, 502, 503):
                raise RewriteError(f"retry {r.status_code}: {r.text[:150]}")
            if r.status_code >= 400:
                raise RewriteError(f"Gemini {r.status_code}: {r.text[:200]}")
            j = r.json()
            block = (j.get("promptFeedback") or {}).get("blockReason")
            if block:
                raise RewriteError(f"Gemini отклонил запрос: {block}")
            cand = (j.get("candidates") or [{}])[0]
            reason = cand.get("finishReason")
            if reason == "MAX_TOKENS":
                raise IncompleteRewrite("Gemini достиг лимита ответа. Обрезанный результат не принят; нужна меньшая часть сценария.")
            if reason and reason != "STOP":
                raise RewriteError(f"Gemini не завершил рерайт (finishReason={reason}); результат не принят.")
            text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts") or [] if not p.get("thought"))
            if not text.strip():
                raise RewriteError(f"retry пустой ответ Gemini, finishReason={reason}")
            return text
        return _retry(go)
    if engine == "fastgen":
        def go(model=model):
            r = requests.post("https://api.fast-gen.ai/v1/chat/completions", headers={"X-API-Key": key},
                              json={"model": model, "temperature": 0.8, "max_tokens": max_tokens,
                                    "messages": [{"role": "user", "content": prompt}]}, timeout=600)
            if r.status_code == 429 or r.status_code >= 500:
                raise RewriteError(f"retry {r.status_code}: {r.text[:150]}")
            if r.status_code >= 400:
                raise RewriteError(f"fast-gen chat {r.status_code}: {r.text[:200]}")
            return completion_text(r.json())
        try:
            return _retry(go, tries=2)
        except ModelUnavailable:
            return _retry(lambda: go("google/gemini-3.6-flash"))
    raise RewriteError("Неизвестный движок: " + str(engine))


def clean_text(t):
    """Убираем markdown-мусор, кавычки-обёртки и служебные строки."""
    junk = re.compile(r"code_reference|code_event_index|\[file-tag|^\s*word count\s*:|^```|^\s*[?&=\w]*\s*$|сценарий .*готов\s*:?\s*$", re.I)
    lines = [l for l in t.splitlines() if not l.strip() or not junk.search(l)]
    t = "\n".join(lines).strip()
    t = re.sub(r'^\s*\w+\s*=\s*(?:"""|\'\'\')', "", t)  # модель иногда оборачивает: text = """..."""
    t = re.sub(r'(?:"""|\'\'\')\s*$', "", t)
    t = re.sub(r"\[[^\]\n]*[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}[^\]\n]*\]", "", t)  # служебные ссылки вида [... uuid]
    t = re.sub(r"^\s*#+\s*", "", t, flags=re.M)
    t = t.replace("**", "").replace("__", "")
    t = re.sub(r"[ 	]*:{2,}[ 	]*$", "", t, flags=re.M)  # хвосты вида « :::» от некоторых моделей
    # служебные абзацы в начале («Вот переписанный сценарий:», «Принял задачу…») и в конце («Итого слов…»)
    meta = re.compile(r"вот |ниже |готов|переписан|сценари|версия|принял|проанализ|хук|вступлени|here is|here's|rewritten|script|title|"
                      r"название|итого|слов[:\s]|word|конец|the end|надеюсь|если нужно|если хотите", re.I)
    paras = [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]
    while len(paras) > 1 and len(paras[0].split()) < 30 and meta.search(paras[0]):
        paras.pop(0)
    while len(paras) > 1 and len(paras[-1].split()) < 30 and meta.search(paras[-1]):
        paras.pop()
    if paras and paras[0].endswith(":") and len(paras[0].split()) < 12:
        paras.pop(0)
    return "\n\n".join(paras).strip()


def rewrite_key(source_text, words, language, engine):
    return hashlib.sha1(f"v2|{engine}|{language}|{words}|{source_text.strip()}".encode("utf-8")).hexdigest()[:10]


def rewrite(source_text, engine, key, model, words, language, cache_dir, progress=None, *, source_profile=None, preserve_structure=False):
    """Возвращает переписанный сценарий; результат кэшируется по содержимому исходника."""
    if preserve_structure:
        from faithful_rewrite import rewrite_full
        return rewrite_full(source_text, engine, key, model, words, language, cache_dir, progress, source_profile)
    words = max(100, int(words or 1500))
    language = (language or "польском").strip()
    os.makedirs(cache_dir, exist_ok=True)
    cache = os.path.join(cache_dir, f"rewrite_{rewrite_key(source_text, words, language, engine)}.txt")
    source_path = os.path.join(cache_dir, "source_" + hashlib.sha256(source_text.encode("utf-8")).hexdigest()[:16] + ".txt")
    with open(source_path + ".tmp", "w", encoding="utf-8") as source_file:
        source_file.write(source_text)
    os.replace(source_path + ".tmp", source_path)
    if os.path.exists(cache) and os.path.getsize(cache) > 200:
        if progress:
            progress("рерайт уже есть, беру из кэша")
        return open(cache, encoding="utf-8").read()
    name = ENGINE_NAMES.get(engine, engine)
    if words <= PART_WORDS:
        if progress:
            progress(f"{name} переписывает сценарий (~{words} слов, это займёт минуту-две)")
        out = _ask(engine, key, model, lang_rule(language) + "\n\n" + PROMPT.format(language=language, words=words, script=source_text.strip())
                   + "\n\n" + lang_rule(language), words, language, progress)
    else:
        # Длинный сценарий одним ответом модели не пишут («too long for a single response») — пишем частями
        n = -(-words // PART_WORDS)
        per = -(-words // n)
        parts = []
        for i in range(1, n + 1):
            if progress:
                progress(f"{name} пишет часть {i} из {n} (~{per} слов)")
            tail = " ".join(" ".join(parts).split()[-350:]) if parts else ""
            prompt = lang_rule(language) + "\n\n" + PART_PROMPT.format(language=language, words=words, per=per, i=i, n=n, script=source_text.strip(),
                                        tail=(tail or "(это первая часть)"),
                                        ending="Это последняя часть: доведи рассказ до конца и заверши его." if i == n
                                        else "Это НЕ последняя часть: не завершай рассказ, остановись на месте, с которого удобно продолжить.")
            prompt += "\n\n" + lang_rule(language)
            parts.append(_ask(engine, key, model, prompt, per, language, progress))
        out = "\n\n".join(parts)
    with open(cache, "w", encoding="utf-8") as f:
        f.write(out)
    return out


PART_WORDS = 1200
REFUSAL = re.compile(r"too long|single response|cannot (?:fit|write)|can't (?:fit|write)|не могу (?:уместить|написать)|слишком длинн"
                     r"|^\s*(?:принял задачу|понял|хорошо|конечно|sure|certainly|okay|ok\b|i(?:'ll| will) (?:analy|write|rewrite)|я проанализировал)", re.I)
PART_PROMPT = PROMPT + """

ВАЖНО: итоговый сценарий большой (~{words} слов), поэтому пишем его по частям. Сейчас напиши ТОЛЬКО часть {i} из {n}, примерно {per} слов.
Конец предыдущей части (продолжай ровно с этого места, не повторяй его и не пересказывай начало):
{tail}
{ending}
Никаких заголовков, номеров частей и пояснений — только текст сценария."""


def fix_language(engine, key, model, text, language, progress=None):
    """Абзацы не на том языке переводим отдельным запросом; остальное не трогаем."""
    bad, paras = bad_paragraphs(text, language)
    if not bad:
        return text
    en, native, _ = LANG_INFO.get(language, (language, language, None))
    if progress:
        progress(f"часть текста получилась не на том языке — перевожу {len(bad)} абзац(ев) на {native}")
    out, i = [], 0
    while i < len(paras):
        if i not in bad:
            out.append(paras[i])
            i += 1
            continue
        j = i
        while j + 1 < len(paras) and (j + 1) in bad:  # соседние плохие абзацы переводим одним куском — связность
            j += 1
        chunk = "\n\n".join(paras[i:j + 1])
        tr = clean_text(chat_text(engine, key, model, f"Translate the following text into {en} ({native}). Keep every sentence, the meaning, "
                                  f"the tone and the paragraph breaks. Numbers stay written out in words. Output ONLY the translation, "
                                  f"nothing else.\n\n{chunk}", max_tokens=min(60000, len(chunk.split()) * 6 + 800)))
        out.append(tr if tr and not wrong_language(tr, language) else chunk)
        i = j + 1
    return "\n\n".join(out)


def _ask(engine, key, model, prompt, want, language=None, progress=None):
    """Один запрос к модели с проверкой: отказ, слишком короткий ответ или не тот язык → повтор, потом запасная модель."""
    last = ""
    for attempt in range(3):
        netutil.check_cancel()
        use_model = model
        if attempt == 2 and engine == "fastgen":
            use_model = "google/gemini-3.6-flash"  # третья попытка — другой моделью
        out = clean_text(chat_text(engine, key, use_model, prompt, max_tokens=min(60000, want * 4 + 800)))
        if not REFUSAL.search(out[:300]) and len(out.split()) >= want * 0.3:
            if language:
                bad, paras = bad_paragraphs(out, language)
                if bad and len(bad) > len(paras) * 0.6 and attempt < 2:
                    last = out  # почти всё не на том языке — проще переспросить
                    netutil.pause(2)
                    continue
                out = fix_language(engine, key, use_model, out, language, progress)
            return out
        last = out
        netutil.pause(3)
    if language and last and len(last.split()) >= want * 0.3 and not REFUSAL.search(last[:300]):
        return fix_language(engine, key, model, last, language, progress)
    raise RewriteError("Модель не справилась с объёмом даже по частям: " + last[:160])
