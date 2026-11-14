"""Deterministic editorial clip selection. No provider or model calls."""
import re

STRATEGIES = {'legacy', 'key', 'hook', 'manual', 'local'}


def settings(value=None):
    value = value or {}
    if not isinstance(value,dict):raise ValueError('Некорректные настройки оживления.')
    strategy = value.get('strategy', 'legacy')
    limit = value.get('limit', 10)
    if strategy not in STRATEGIES or type(limit) is not int or not 0 <= limit <= 150:
        raise ValueError('Выбор оживления: неизвестный режим или лимит вне 0–150.')
    return {'strategy': strategy, 'limit': limit}


def suitability(scene):
    raw = ' '.join(str(scene.get(k) or '') for k in ('prompt', 'raw_prompt', 'source_redesign', 'source_motion', 'motion'))
    if scene.get('shot_size') == 'diagram' or re.search(r'diagram|infographic|схем|инфограф|whiteboard', raw, re.I):
        return 'Схема: движение может исказить детали и подписи'
    if re.search(r'colli\w*|crash\w*|impact|столкнов\w*|авари\w*|наезд\w*|three.legs|размах\w*|head.turn|тряс\w*.голов', raw, re.I):
        return 'Сложное взаимодействие: риск искажения людей или объектов'
    return ''


def select(scenes, value=None, mode='images', video_first=0):
    config = settings(value)
    strategy, limit = config['strategy'], config['limit']
    rows = []
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
        elif strategy == 'local':row['reason'] = 'Картинка с локальным движением камеры'
        rows.append(row)
    if strategy in ('key', 'hook'):
        candidates = [row for row in rows if not row['risk']]
        picked = []
        used = set()
        while candidates and len(picked) < limit:
            # Spread clips over the story while keeping strong narrative roles first.
            best = max(candidates, key=lambda r:(r['_score'] - sum(max(0, 15-abs(r['_position']-p['_position'])*5) for p in picked), -r['_position']))
            candidates.remove(best)
            if best['id'] in used:continue
            best['selected'] = True; picked.append(best); used.add(best['id'])
        for row in rows:
            if not row['selected']:row['reason'] = row['risk'] or 'За пределами лимита видеовставок'
    count = sum(row['selected'] for row in rows)
    if strategy == 'manual' and count > limit:
        raise ValueError(f'Выбрано {count} видеовставок при лимите {limit}. Снимите отметки или увеличьте лимит.')
    for row in rows:
        row.pop('_score');row.pop('_position')
    return {'rows':rows, 'count':count, **config}


def apply(scenes, value=None, mode='images', video_first=0):
    config = settings(value)
    if config['strategy'] == 'legacy':return scenes
    result = select(scenes, config, mode, video_first)
    return [{**scene, '_animate':row['selected'], 'animation_reason':row['reason']} for scene, row in zip(scenes, result['rows'])]
