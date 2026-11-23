"""Reference-informed native narration and reusable opening audio.

Planning is local after the cached audio observations. It never rewrites a script
or invokes an image/video/ASR model. TTS performance still needs human listening.
"""
import copy
import hashlib
import json
from pathlib import Path
import re

import narration_rules as rules
import netutil
import source_narration

VERSION = 1


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def script_hash(text):
    return hashlib.sha256(text.strip().encode()).hexdigest()


def directory(root):
    return Path(root) / 'jobs' / 'speech_plans'


def capabilities(service, model=''):
    if service == 'fastgen' or service == 'elysium' and model.startswith('google/'):
        return 'Модель получает промпт подачи, язык и регион. Результат нужно прослушать.'
    if service == 'lumean':
        return 'Lumean получает язык, настройки скорости и выразительности, а также краткие указания в поддерживаемых тегах чтения. Теги оплачиваются как символы. Отдельного поля для полного промпта нет; произношение и регион проверьте на пробе.'
    if service == 'elevenlabs' or service == 'elysium':
        return 'Передаются доступные настройки темпа и выразительности. Свободный промпт эта модель не принимает; нужен голос с подходящим произношением.'
    if service == 'edge':
        return 'Используется голос выбранного языка и региона, настраивается скорость. Edge не поддерживает свободный промпт или точные смысловые акценты.'
    raise ValueError('Для пробы по референсу выберите Lumean, Gemini TTS, Elysium, ElevenLabs или Edge.')


def check_voice(service, voice, locale):
    if service == 'edge' and voice and locale:
        if not voice.casefold().startswith(locale.casefold() + '-'):
            raise ValueError('Голос Edge не соответствует региону '+locale+'. Выберите подходящий голос или очистите ID для автоматического выбора.')


def lumean_direction(locale,language,delivery,notes=''):
    """Lumean's documented […] narrator hints; never put the full plan in input_text."""
    names={'ro':'Romanian','es':'Spanish','pt':'Portuguese'}
    native=rules.LOCALES[locale][2] if locale else 'Native '+names.get(language,language)+' pronunciation.'
    manner='Calm, restrained and conversational.' if delivery['energy']<.45 else 'Confident, clear and conversational.'
    # Prevent nested brackets/newlines from turning a note into spoken content.
    direction=native+' '+manner+' Natural sentence emphasis and breathing, no sing-song rhythm.'
    if notes:direction+=' '+notes
    direction=re.sub(r'[\[\]\r\n]',' ',direction)
    return '['+direction+']'


def build(text, language, locale, delivery, notes=''):
    text=text.strip()
    code=rules.language_code(language)
    if not text or len(text)>160000:
        raise ValueError('Подготовьте сценарий: от 1 до 160 000 символов.')
    if not code:
        raise ValueError('Выберите язык готового сценария.')
    locale=rules.locale_for(code,locale=locale)
    if rules.wrong_script(text,code):
        raise ValueError('Письменность сценария не соответствует выбранному языку. Сначала подготовьте текст на нужном языке.')
    delivery=source_narration.validate(delivery)
    if not isinstance(notes,str) or len(notes)>2500:
        raise ValueError('Указания к чтению: не более 2500 символов.')
    adjusted=rules.voice_direction(delivery,code)
    label=rules.LOCALES[locale][1] if locale else code
    prompt=('NARRATOR PERFORMANCE DIRECTIONS — NEVER SPEAK THESE DIRECTIONS.\n'
            'Read only the supplied narration, verbatim, in '+label+' ('+(locale or code)+'). '
            'Use a native pronunciation throughout, never the reference speaker\'s foreign accent. '
            'Do not translate or paraphrase during synthesis.\n'+rules.localisation(code,locale)+'\n'
            'REFERENCE DELIVERY OBSERVED FROM AUDIO: '+delivery['instructions']+'\n'
            'Transfer relative energy, restraint, contrast and conversational intent. Adapt the rhythm to the target language; '
            'never force Russian word stress, phrase melody or source words-per-minute onto another language. '
            'Begin clearly, without a theatrical announcer voice. Use punctuation and sentence meaning for breath groups; '
            'give important new information a single natural emphasis, let questions sound like questions and allow a brief pause '
            'before the explanation/payoff. Avoid stressing every word, exaggerated vowels or a repeated sing-song cadence. '
            'Keep names, numbers, negation and technical terms intelligible. Do not invent spoken filler, sound effects, '
            'laughter or nonverbal tags. No [pause] or SSML in spoken text. Keep delivery continuous between passages.')
    if notes.strip():prompt+='\nAUTHOR\'S READING DIRECTIONS (not spoken): '+notes.strip()
    # Split once, before either preview or full production. Keep every character
    # and use the identical boundaries for both paths, even after restarting.
    boundaries=[];start=0
    for match in re.finditer(r'(?<=[.!?…])\s+|\n\s*\n',text):
        if match.end()-start>=650:
            boundaries.append((start,match.end()));start=match.end()
    if start<len(text):boundaries.append((start,len(text)))
    passages=[]
    for a,b in boundaries:
        # Bound exceptionally long sentences without dropping any whitespace.
        while b-a>1500:
            cut=text.rfind(' ',a+650,a+1400)
            if cut<0:cut=a+1400
            passages.append(text[a:cut]);a=cut
        if text[a:b].strip():passages.append(text[a:b])
    assert ''.join(passages)==text
    return {'version':VERSION,'script':text,'script_sha256':script_hash(text),'language':code,
            'locale':locale,'delivery':delivery,'adjusted_delivery':adjusted,'notes':notes.strip(),
            'prompt':prompt,'passages':passages}


def save(root, plan):
    key=digest(plan)
    folder=directory(root)/key;folder.mkdir(parents=True,exist_ok=True)
    source_narration._atomic(folder/'plan.json',plan)
    return key


def load(root, key):
    if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key):
        raise ValueError('Сначала подготовьте подачу озвучки.')
    try:plan=json.loads((directory(root)/key/'plan.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):raise ValueError('План подачи не найден. Подготовьте его заново; готовые аудиофрагменты сохраняются.') from None
    if digest(plan)!=key or plan.get('version')!=VERSION:
        raise ValueError('План подачи повреждён или устарел.')
    return plan


def bind(cfg, root, text):
    """Opt-in plan: refuse stale text instead of billing a new unreviewed opening."""
    key=cfg.get('speech_plan_id')
    if not key:return None
    if cfg.get('speech_plan_dirty'):
        raise ValueError('Указания к озвучке изменились. Подготовьте подачу заново в пункте 4 или отключите план подачи.')
    plan=load(root,key)
    if plan['script_sha256']!=script_hash(text):
        raise ValueError('Сценарий изменился после пробы. В пункте 4 подготовьте подачу и прослушайте новое начало либо отключите план подачи.')
    locale=rules.locale_for(plan['language'],cfg)
    if locale!=plan['locale']:
        raise ValueError('Регион озвучки изменился. Подготовьте новую подачу в пункте 4.')
    return plan


def render(root, plan, cfg, service, voice, progress, *, preview=False):
    """Synthesize only enough immutable passages for 30 s, reuse them in full."""
    import elysium_replacement as replacement
    import pipeline
    effective=replacement.effective(cfg)
    actual=replacement.service(effective,service)
    model=effective.get('elysium_tts_model','') if actual=='elysium' else ''
    capabilities(actual,model)
    actual_voice=effective.get('elysium_tts_voice') if actual=='elysium' else voice
    check_voice(actual,actual_voice,plan['locale'])
    identity=digest([digest(plan),actual,actual_voice,model,effective.get('elysium_base_url') if actual=='elysium' else None])
    folder=directory(root)/digest(plan)/'audio'/identity;folder.mkdir(parents=True,exist_ok=True)
    files=[];seconds=0.
    for i,part in enumerate(plan['passages']):
        netutil.check_cancel()
        piece=folder/f'{i:04d}';piece.mkdir(exist_ok=True)
        local={**copy.deepcopy(cfg),'_speech_chunk':True,'_tts_language':plan['language'],
               '_speech_locale':plan['locale'],'_speech_prompt':plan['prompt'],
               '_source_delivery':plan['delivery'],
               '_speech_previous':plan['passages'][i-1][-1000:] if i else '',
               '_speech_next':plan['passages'][i+1][:1000] if i+1<len(plan['passages']) else '',
               '_speech_notes':plan['notes']}
        audio=pipeline.make_voiceover(local,part,service,voice,str(piece),progress)
        files.append(audio);seconds+=pipeline.audio_duration(audio)
        if preview and seconds>=30:break
    out=folder/('opening30.mp3' if preview else 'full.mp3')
    receipt={'inputs':[(str(Path(f).relative_to(folder)),hashlib.sha256(Path(f).read_bytes()).hexdigest()) for f in files],
             'preview':preview,'version':VERSION}
    try:cached=json.loads(out.with_suffix('.json').read_text(encoding='utf-8'))==receipt and out.is_file() and pipeline.audio_duration(str(out))>0
    except (OSError,ValueError):cached=False
    if not cached:
        netutil.check_cancel()
        # Relative filenames only; no user path interpolation into ffmpeg lists.
        listing=folder/('opening-list.txt' if preview else 'full-list.txt')
        listing.write_text(''.join("file '"+Path(f).relative_to(folder).as_posix()+"'\n" for f in files),encoding='utf-8')
        args=['-f','concat','-safe','0','-i',str(listing)]
        if preview:args+=['-t','30']
        pipeline.run_ffmpeg(args+['-vn','-c:a','libmp3lame','-b:a','128k','-f','mp3',str(out)+'.part'])
        if pipeline.audio_duration(str(out)+'.part')<=0:raise ValueError('Пустая проба озвучки.')
        netutil.check_cancel();Path(str(out)+'.part').replace(out)
        source_narration._atomic(out.with_suffix('.json'),receipt)
    return str(out)
