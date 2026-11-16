"""A reusable, approximate motion language learnt from short temporal samples."""
import copy
import math
from pathlib import Path

STYLES=('static','push-in','pull-out','pan-left','pan-right','pan-up','pan-down')
VERSION=1


def validate(value):
    if not isinstance(value,dict):raise ValueError('Профиль оживления не найден. Сначала подготовьте исходник или изучите движение в дизайне канала.')
    styles=value.get('camera_styles');share=value.get('subject_motion_share');strength=value.get('strength')
    if not isinstance(styles,list) or not 1<=len(styles)<=12 or any(s not in STYLES for s in styles):raise ValueError('Некорректные движения камеры в профиле.')
    if any(type(n) not in (int,float) or not math.isfinite(n) for n in (share,strength)) or not 0<=share<=1 or not 0<=strength<=.15:raise ValueError('Некорректная интенсивность оживления.')
    instructions=value.get('instructions','');confidence=value.get('confidence')
    if not isinstance(instructions,str) or not 1<=len(instructions)<=1800 or confidence not in ('low','medium','high'):raise ValueError('Не получено обоснованное описание движения.')
    return {'version':VERSION,'camera_styles':styles[:],'subject_motion_share':float(share),'strength':float(strength),
            'instructions':instructions,'confidence':confidence,
            'limitations':'Характер движения оценён по выборочным коротким отрезкам, не точное воспроизведение монтажа или движений исходника.'}


def analyse(video,cfg,directory,progress=None):
    import source_analysis as sa
    import storyboard as sb
    from PIL import Image,ImageOps,ImageDraw
    engine,key,model=sa._provider(cfg);duration=sa._duration(video)
    if not math.isfinite(duration) or duration<.3:raise ValueError('Образец слишком короткий для анализа оживления.')
    identity=sb.digest([VERSION,sa._hash_file(video),engine,model,'temporal-motion'])
    folder=Path(directory)/('motion_'+identity);folder.mkdir(parents=True,exist_ok=True)
    cached=folder/'profile.json'
    try:
        import json
        result=validate(json.loads(cached.read_text(encoding='utf-8')))
        if progress:progress('характер оживления уже изучен, использую сохранённый профиль')
        return result
    except (OSError,ValueError):pass
    end=max(0,duration-.05);span=min(.8,duration*.2)
    centres=[duration*(i+.5)/6 for i in range(6)]
    times=[max(0,min(end,t+d)) for t in centres for d in (-span,0,span)]
    if progress:progress('изучаю движение: 6 коротких отрезков по 3 кадра; это дополнительный запрос анализа')
    frames=sa.extract_frames(video,folder/'frames',times)
    sheet=folder/'temporal.jpg';canvas=Image.new('RGB',(1200,6*245),'#202020');draw=ImageDraw.Draw(canvas)
    for i,path in enumerate(frames):
        with Image.open(path) as image:
            image=ImageOps.contain(image.convert('RGB'),(400,220));canvas.paste(image,((i%3)*400,(i//3)*245))
        draw.text(((i%3)*400+4,(i//3)*245+222),f'{times[i]:.2f}s',fill='white')
    canvas.save(sheet,quality=85)
    prompt=('Each ROW shows THREE chronologically ordered frames from ONE short source-video excerpt. '
            'Rows sample the whole video, not consecutive shots. Compare movement WITHIN rows only. '
            'Distinguish camera pan/zoom on still art from genuine subject/articulated motion and from a cut. '
            'A cut is NOT motion: ignore rows crossing cuts. Infer only visible evidence. If uncertain use low confidence '
            'and a conservative still/camera profile; never guess that neural animation was used. '
            'Describe reusable motion language, NOT source footage, characters or exact choreography. '
            'camera_styles is a short repeating mix using static|push-in|pull-out|pan-left|pan-right|pan-up|pan-down. '
            'subject_motion_share is the approximate share of usable samples with visible subject movement, 0..1. '
            'strength is conservative camera crop/zoom intensity 0..0.15. instructions describes restraint, pacing '
            'and when motion is purposeful. Avoid invented motion, head bobbing, deformation or complex interaction. '
            'Return JSON {"camera_styles":["static","push-in"],"subject_motion_share":0.0,"strength":0.06,'
            '"instructions":"English reusable motion guidance","confidence":"low|medium|high"}.')
    result=sa.cached_query(folder,engine,key,model,prompt,sheet,validate=validate,progress=progress)
    result=validate(result);sb.atomic(cached,result);return result


def attach(scenes,profile):
    data=(profile or {}).get('reference_animation')
    return [{**s,'reference_animation':copy.deepcopy(data)} for s in scenes]


def local_path(kind,duration,strength):
    if kind not in STYLES:raise ValueError('Неизвестное движение камеры.')
    if kind=='static' or not strength:return None
    def path(t):
        p=max(0,min(1,t/max(.1,duration)))
        if kind=='push-in':return 1+strength*p,.5,.5
        if kind=='pull-out':return 1+strength*(1-p),.5,.5
        return 1+strength,1-p if kind=='pan-left' else p if kind=='pan-right' else .5,1-p if kind=='pan-up' else p if kind=='pan-down' else .5
    return path
