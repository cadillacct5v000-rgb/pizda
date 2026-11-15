"""Explicit FastGen replacement settings. Applying these never changes media cache IDs."""
import copy
import re

DEFAULTS={
 'elysium_replace_fastgen':False,
 'elysium_model':'gpt-4.1-mini',
 'elysium_image_model':'google/gemini-3.1-flash-image-preview',
 'elysium_stt_model':'google/gemini-3.6-flash',
 'elysium_tts_model':'google/gemini-3-8-flash-tts',
 'elysium_tts_voice':'Charon',
}
IMAGE_MODELS=('google/gemini-3.1-flash-image-preview','google/gemini-3-pro-image')
TTS_MODELS=('google/gemini-3-8-flash-tts','google/gemini-3-1-flash-tts','google/gemini-2-5-pro-tts',
            'elevenlabs/text-to-speech-v3-provider-4','elevenlabs/text-to-speech-multilingual-v2-provider-4')


def settings(cfg=None,opts=None):
    merged={**(cfg or {}),**{k:v for k,v in (opts or {}).items() if k in DEFAULTS}}
    result={k:merged.get(k,v) for k,v in DEFAULTS.items()}
    if type(result['elysium_replace_fastgen']) is not bool:raise ValueError('Выберите обычный режим или замену FastGen.')
    if result['elysium_image_model'] not in IMAGE_MODELS:raise ValueError('Выберите поддерживаемую модель картинок Elysium.')
    if result['elysium_tts_model'] not in TTS_MODELS:raise ValueError('Выберите поддерживаемую модель озвучки Elysium.')
    for name in ('elysium_model','elysium_stt_model','elysium_tts_voice'):
        if not isinstance(result[name],str) or not re.fullmatch(r'[\w./-]{1,160}',result[name]):raise ValueError('Некорректная модель или голос Elysium.')
    if not re.fullmatch(r'(?:google/)?gemini-[A-Za-z0-9._-]+',result['elysium_stt_model']):raise ValueError('Для распознавания выберите Gemini с аудиовходом.')
    return result


def effective(cfg,opts=None):
    result=copy.deepcopy(cfg);result.update(settings(cfg,opts))
    if not result['elysium_replace_fastgen']:return result
    if not result.get('elysium_api_key'):raise ValueError('Для замены FastGen введите ключ Elysium.')
    for name in ('llm_engine','stt_engine','rewrite_engine','source_analysis_engine'):
        if result.get(name,'fastgen')=='fastgen':
            result[name]='elysium'
            if name=='source_analysis_engine':result['source_analysis_model']=result['elysium_stt_model']
    if result.get('video_engine','providers')=='providers':result['video_engine']='elysium'
    # Never construct a FastGen fallback client in this mode, even if its old key is retained on disk.
    result['fastgen_api_key']=''
    if not isinstance(result.get('image_keys'),dict):result['image_keys']={}
    result['image_keys']['fastgen']=[]
    return result


def service(cfg,name):
    return 'elysium' if cfg.get('elysium_replace_fastgen') and name=='fastgen' else name
