"""Deterministic editorial clip selection. No provider or model calls."""
import re

STRATEGIES = {'legacy', 'key', 'hook', 'manual', 'local','reference'}


def settings(value=None):
    if value is None:value = {}
    if not isinstance(value,dict):raise ValueError('Некорректные настройки оживления.')
    strategy = value.get('strategy', 'legacy')
    limit = value.get('limit', 10)
    if not isinstance(strategy,str) or strategy not in STRATEGIES or type(limit) is not int or not 0 <= limit <= 150:
        raise ValueError('Выбор оживления: неизвестный режим или лимит вне 0–150.')
    return {'strategy': strategy, 'limit': limit}


def suitability(scene):
    visual = scene.get('source_redesign') or scene.get('raw_prompt') or scene.get('prompt') or ''
    # Appended design rules mention diagrams even in ordinary live-action shots.
    visual = str(visual).split('APPROVED CHANNEL DESIGN,',1)[0]
    raw = visual + ' ' + str(scene.get('source_motion') or scene.get('motion') or '')
    if scene.get('shot_size') == 'diagram' or re.search(r'diagram|infographic|схем|инфограф|whiteboard', raw, re.I):
        return 'Схема: движение может исказить детали и подписи'
    if re.search(r'colli\w*|crash\w*|impact|столкнов\w*|авари\w*|наезд\w*|three.legs|размах\w*|head.turn|тряс\w*.голов', raw, re.I):
        return 'Сложное взаимодействие: риск искажения людей или объектов'
    return ''


def select(scenes, value=None, mode='images', video_first=0):
    config = settings(value)
    strategy, limit = config['strategy'], config['limit']
    rows = []
    reference=None
    if strategy=='reference' and scenes:
        import reference_animation
        reference=reference_animation.validate(scenes[0].get('reference_animation') or (scenes[0].get('channel_design') or {}).get('reference_animation'))
        share=reference['subject_motion_share'] if reference['confidence']!='low' else 0
        if (scenes[0].get('channel_design') or {}).get('motion_policy')=='local':share=0
        limit=min(limit,round(len(scenes)*share))
    for i, scene in enumerate(scenes):
        risk = suitability(scene)
        role = scene.get('editorial_role', '')
        score = {'hook': 100, 'reveal': 95, 'contrast': 85, 'resolve': 65, 'example': 45}.get(role, 25)
        reason = {'hook':'Вступление', 'reveal':'Раскрытие важного момента', 'contrast':'Смысловой поворот', 'resolve':'Вывод', 'example':'Наглядный пример'}.get(role, 'Визуальный акцент')
        if i == 0:
            score += 35; reason = 'Первый кадр'
        if strategy == 'hook' and i < min(3, max(1, len(scenes)//5)):
            score += 65; reason = 'Начало ролика'
        row = {'index':scene.get('index', i), 'id':scene.get('plan_id', scene.get('id', str(i))),
               'selected':False, 'reason':reason, 'risk':risk, '_score':score, '_position':i}
        if strategy == 'legacy':
            design = scene.get('channel_design') or {}
            row['selected'] = design.get('motion_policy', 'generated') != 'local' and (mode == 'video' or i < video_first or (mode == 'mixed' and i % 2 == 0))
            row['reason'] = 'По выбранному режиму'
        elif strategy == 'manual':
            row['selected'] = scene.get('animate') is True
            row['reason'] = 'Выбрано вручную' if row['selected'] else 'Оставить картинкой'
        elif strategy=='reference':row['reason']='Характер камеры по исходнику; без нейроклипа'
        elif strategy == 'local':row['reason'] = 'Картинка с локальным движением камеры'
        rows.append(row)
    if strategy in ('key', 'hook','reference'):
        candidates = [row for row in rows if not row['risk']]
        picked = []
        for row in candidates:row['_rank']=row['_score']
        used = set()
        while candidates and len(picked) < limit:
            # Spread clips over the story while keeping strong narrative roles first.
            best = max(candidates, key=lambda r:(r['_rank'], -r['_position']))
            candidates.remove(best)
            if best['id'] in used:continue
            best['selected'] = True
            if strategy=='reference':best['reason']='Движение объектов по характеру исходника · нейроклип'
            picked.append(best); used.add(best['id'])
            for row in candidates:
                row['_rank']-=max(0,15-abs(row['_position']-best['_position'])*5)
        for row in rows:
            if not row['selected']:row['reason'] = row['risk'] or ('По профилю исходника — локальная камера без нейроклипа' if strategy=='reference' else 'За пределами лимита видеовставок')
    count = sum(row['selected'] for row in rows)
    if strategy == 'manual' and count > limit:
        raise ValueError(f'Выбрано {count} видеовставок при лимите {limit}. Снимите отметки или увеличьте лимит.')
    for row in rows:
        row.pop('_score');row.pop('_position');row.pop('_rank',None)
    return {'rows':rows, 'count':count, **config}


def apply(scenes, value=None, mode='images', video_first=0):
    config = settings(value)
    if config['strategy'] == 'legacy':return scenes
    result = select(scenes, config, mode, video_first)
    output=[{**scene, '_animate':row['selected'], 'animation_reason':row['reason']} for scene, row in zip(scenes, result['rows'])]
    if config['strategy']=='reference':
        import reference_animation
        for i,scene in enumerate(output):
            reference=reference_animation.validate(scene.get('reference_animation') or (scene.get('channel_design') or {}).get('reference_animation'))
            kind=reference['camera_styles'][i%len(reference['camera_styles'])]
            scene['_local_motion']={'kind':kind,'strength':reference['strength']}
            if scene['_animate']:scene['source_motion']=scene['motion']='Restrained purposeful motion. '+reference['instructions']
    return output
