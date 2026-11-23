"""Cached editorial adaptation: evidence from a reference, independent story and art."""
import copy
import json
from pathlib import Path
import re
import unicodedata

import netutil
import storyboard as sb

VERSION = 1
BRIEF = ('REFERENCE-LED ORIGINAL VIDEO. Retain the topic, audience need and useful explanatory principles, '
         'not the source script, shot sequence, distinctive jokes, characters, logos or compositions. '
         'Write a new hook, organise the explanation around an independent angle, add useful reasoning '
         'and clearly hypothetical analogies, and deliver a concrete payoff. Never invent evidence, '
         'statistics, studies, quotations or factual examples. Source claims are unverified claims, '
         'not independently checked facts. Do not claim measured retention or guaranteed YouTube approval. '
         'The approved own visual design overrides source palette, typography and character identity. '
         'Preserve the useful level of abstraction: faceless human references stay faceless, rather than gaining realistic faces. '
         'Design own recurring characters and vehicles, then keep each entity consistent across scenes. ')


def _strings(value, limit=12):
    return (isinstance(value, list) and 0 < len(value) <= limit
            and all(isinstance(x, str) and 0 < len(x.strip()) <= 1200 for x in value))


def _normal(text):
    value=unicodedata.normalize('NFKC',str(text)).translate(str.maketrans({'’':"'",'‘':"'",'“':'"','”':'"','–':'-','—':'-','−':'-','\u00ad':''}))
    return ' '.join(value.casefold().split())


def _query(client, prompt, path, validate, progress):
    import studio
    import model_reply
    value = studio._query(client, prompt, path, progress)
    try:
        validate(value)
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        sb.atomic(path.with_suffix('.received.json'),{'stage':path.stem,'reason':str(error) or type(error).__name__,'result':value})
        path.unlink(missing_ok=True)
        model_reply.invalidate(path.with_suffix('.reply.json'))
        raise ValueError('Модель вернула непроверяемый план самостоятельного ролика. Этап '+path.stem+': '+(str(error) or type(error).__name__)+'. '
                         'Успешные этапы сохранены; озвучка и картинки не запускались.') from None
    return value


def _source_units(chunk):
    from faithful_rewrite import source_chunks
    return [{'id':f'e{i}','text':part} for i,part in enumerate(source_chunks(chunk,120))]


def _fact_item(item,units):
    if not isinstance(item,dict):raise ValueError('утверждение должно быть объектом')
    statement=item.get('statement',item.get('claim'))
    if not isinstance(statement,str) or not 1<=len(statement.strip())<=1200:raise ValueError('нет текста statement или он длиннее 1200 символов')
    note=item.get('review_note','')
    if not isinstance(note,str) or len(note)>1200:raise ValueError('review_note должен быть строкой до 1200 символов')
    ids=item.get('source_ids',item.get('source_id'))
    if isinstance(ids,str):ids=[ids]
    if ids is not None:
        known={unit['id']:unit['text'] for unit in units}
        if not isinstance(ids,list) or not 1<=len(ids)<=12 or any(not isinstance(key,str) or key not in known for key in ids):raise ValueError('source_ids содержит неизвестные номера фрагментов')
        ids=list(dict.fromkeys(ids));quote=' '.join(known[key] for key in ids)
    else:
        quote=item.get('source_excerpt')
        source=''.join(unit['text'] for unit in units)
        if not isinstance(quote,str) or not 8<=len(quote)<=900 or _normal(quote) not in _normal(source):raise ValueError('source_excerpt не найден дословно в исходнике; нужны source_ids, а не переписанная цитата')
        ids=[]
    result={'statement':statement.strip(),'source_excerpt':quote,'review_note':note}
    if ids:result['source_ids']=ids
    return result


def _fact_parts(value,units):
    items=value.get('facts',value.get('claims')) if isinstance(value,dict) else None
    if not isinstance(items,list) or not 1<=len(items)<=64:raise ValueError('нужен непустой массив facts (до 64 утверждений)')
    accepted={};rejected=[]
    for index,item in enumerate(items):
        try:accepted[index]=_fact_item(item,units)
        except ValueError as error:rejected.append({'index':index,'reason':f'утверждение №{index+1}: {error}','item':item})
    return accepted,rejected,items


def _facts_query(client,prompt,path,chunk,progress=None):
    """Recover received claims locally, then repair ONLY broken evidence links once."""
    import studio
    import model_reply
    units=_source_units(chunk);value=None
    try:
        cached=json.loads(path.read_text(encoding='utf-8'))
        value=cached.get('result')
    except (OSError,ValueError,AttributeError):pass
    if value is None:
        # Recover only complete replies; an invalidated reply can also be a refusal.
        try:
            receipt=json.loads(path.with_suffix('.reply.json').read_text(encoding='utf-8'))
            if receipt.get('kind')=='model' and receipt.get('complete') is True:
                from model_json import extract_object
                value=extract_object(receipt['text'])
        except (OSError,ValueError,KeyError,TypeError):pass
    if value is None:value=studio._query(client,prompt,path,progress)
    try:accepted,rejected,items=_fact_parts(value,units)
    except ValueError as error:
        sb.atomic(path.with_suffix('.received.json'),{'stage':path.stem,'reason':str(error),'result':value})
        # Preserve the reply for inspection, but do not trap the next attempt in an unusable envelope.
        path.unlink(missing_ok=True);model_reply.invalidate(path.with_suffix('.reply.json'))
        raise ValueError('Не подготовлены опорные утверждения: '+str(error)+'. Ответ сохранён в '+path.with_suffix('.received.json').name+'. Озвучка и картинки не запускались.') from None
    if rejected:
        sb.atomic(path.with_suffix('.partial.json'),{'accepted':accepted,'rejected':rejected,'result':value})
        statements=[row['item'].get('statement',row['item'].get('claim')) if isinstance(row['item'],dict) else None for row in rejected]
        if any(not isinstance(text,str) or not text.strip() for text in statements):
            path.unlink(missing_ok=True);model_reply.invalidate(path.with_suffix('.reply.json'))
            raise ValueError('Не подготовлены опорные утверждения: '+rejected[0]['reason']+f'. Сохранено {len(accepted)} из {len(items)}; ответ — '+path.with_suffix('.partial.json').name+'. Озвучка и картинки не запускались.')
        if progress:progress(f'уточняю ссылки на исходник только для {len(rejected)} утверждений; {len(accepted)} готовых сохраняются')
        repair_prompt=('Repair ONLY the source links of these existing claims, keeping each statement VERBATIM and in the same order. '
                       'Use only known source_ids from the supplied units. Do not invent evidence or new claims. If a claim is unsupported, '
                       'return source_ids=[]; it will remain rejected. Return {"facts":[{"statement":"unchanged statement","source_ids":["e0"],"review_note":""}]}. '
                       'Reference JSON:\n'+json.dumps({'claims':statements,'source_units':units},ensure_ascii=False))
        repair_path=path.with_suffix('.evidence.json')
        repaired=studio._query(client,repair_prompt,repair_path,progress)
        try:
            fixed,bad,rows=_fact_parts(repaired,units)
            if bad or len(rows)!=len(rejected) or any(row.get('statement',row.get('claim'))!=statement for row,statement in zip(rows,statements)):
                raise ValueError((bad[0]['reason'] if bad else 'исправление изменило утверждения или их количество'))
            for i,broken in enumerate(rejected):
                note=broken['item'].get('review_note','')
                if isinstance(note,str) and len(note)<=1200:fixed[i]['review_note']=note
                accepted[broken['index']]=fixed[i]
        except (ValueError,TypeError,AttributeError) as error:
            sb.atomic(repair_path.with_suffix('.received.json'),{'reason':str(error),'result':repaired})
            repair_path.unlink(missing_ok=True);model_reply.invalidate(repair_path.with_suffix('.reply.json'))
            raise ValueError('Не подтверждены ссылки на исходник: '+str(error)+f'. Готовые утверждения ({len(accepted)}) и исходный ответ сохранены; повтор уточнит только недостающие ссылки. Озвучка и картинки не запускались.') from None
    result={'facts':[accepted[i] for i in range(len(items))]}
    sb.atomic(path,{'signature':sb.digest(prompt),'result':result})
    return result


def overlap_report(source, output):
    """Lexical diagnostic, neither a plagiarism test nor a platform prediction."""
    a = re.findall(r'\w+', source.casefold()); b = re.findall(r'\w+', output.casefold())
    width = 14
    originals = {tuple(a[i:i + width]) for i in range(len(a) - width + 1)}
    matched = set(); examples = []
    for i in range(len(b) - width + 1):
        if tuple(b[i:i + width]) in originals:
            matched.update(range(i, i + width))
            if len(examples) < 5 and (not examples or i > examples[-1][0] + width):
                examples.append((i, ' '.join(b[i:i + width])))
    return {'long_shared_phrases': [text for _, text in examples],
            'substantial_verbatim_overlap': len(b) >= 40 and len(matched) / len(b) >= .35,
            'limitations': 'Совпадения фраз — редакторский сигнал, не проверка авторских прав и не прогноз решения YouTube.'}


def create(text, profile, cfg, directory, client, progress=None, *, language=None):
    """Extract grounded claims once; plan globally; draft only from the selected claims."""
    from faithful_rewrite import source_chunks
    import narration_rules
    import source_narration
    import rewrite
    if not text.strip(): raise ValueError('Исходный текст пуст.')
    profile = profile or {}
    locale=narration_rules.locale_for(language,cfg)
    identity = sb.digest([VERSION, text, profile, cfg.get('editing_pace'), language,locale,narration_rules.VERSION])
    folder = Path(directory) / identity
    folder.mkdir(parents=True, exist_ok=True)
    facts_folder=Path(directory)/'source-facts'/sb.digest([VERSION,text])
    facts_folder.mkdir(parents=True,exist_ok=True)
    presentation=copy.deepcopy((profile.get('adaptation') or {}).get('reference_presentation') or {'narrative_style':profile.get('narrative_style','')[:7000],'voice_direction':source_narration.delivery_of(profile)})
    facts = []
    chunks = source_chunks(text, 1200)
    for i, chunk in enumerate(chunks):
        netutil.check_cancel()
        if progress: progress(f'опорные утверждения исходника: {i + 1}/{len(chunks)}')
        prompt = (BRIEF + 'Extract the important claims and useful explanatory examples from these source units. '
                  'Do not rewrite the narration yet. Cite source_ids exactly as supplied (e0, e1, ...). '
                  'The program attaches the original source text itself: DO NOT retype or translate quotations. '
                  'Omit promotional filler. Distinguish an observation from an inference. Flag questionable numbers, '
                  'names, superlatives, transcription errors or causal claims in review_note; do not pretend to verify them externally. '
                  'Return {"facts":[{"statement":"...","source_ids":["e0"],"review_note":"what needs checking, or empty"}]}, 1–24 concise claims. '
                  'Reference JSON:\n' + json.dumps({'source':_source_units(chunk)}, ensure_ascii=False))
        value = _facts_query(client,prompt,facts_folder / f'facts_{i:04d}.json',chunk,progress)
        for item in value['facts']:
            facts.append({'id': f'f{len(facts) + 1}', **item})
    saved_design = bool(profile.get('channel_design_ref') or cfg.get('_channel_design'))
    design = profile.get('visual_style', {}) if saved_design else None
    visual = profile.get('visual_style', {})
    sample = profile.get('sample') or {}
    targets = min(len(text.split()), max(20, round(float(sample['source_seconds']) * 2.3))) if sample else len(text.split())
    target_sections = max(1, min(24, (targets + 449) // 450))
    own_request=cfg.get('custom_style','') if cfg.get('original_visual')=='custom' else ''
    if cfg.get('original_visual')=='custom' and cfg.get('style') and cfg['style']!='custom':
        import pipeline
        own_request=pipeline.resolve_style(cfg['style'],cfg)[2]
    data = {'claims': facts, 'reference_presentation': presentation,
            'reference_visual_grammar': visual, 'approved_own_design': design,
            'target_words': targets, 'maximum_sections': target_sections,
            'own_style_request': own_request}
    prompt = (BRIEF + narration_rules.writing('plan',language,locale) + (rewrite.lang_rule(language) if language else 'Use the source language for viewer-facing fields. ') + ' Use English only for visual_style. Design a distinct editorial blueprint for the whole video. Learn from useful source '
              'mechanisms (clear stakes, a question/payoff, explanatory visuals) while changing the structure '
              'and expression. Keep and change describe concrete editorial decisions, not vague synonyms. '
              'Use only the supplied claims; every section selects existing fact_ids. Every important claim '
              'must be assigned at least once. Do not duplicate the same explanation in several sections. '
              'If approved_own_design exists, preserve it exactly. Honour own_style_request when supplied. Otherwise propose an own coherent visual '
              'system inspired by broad reference grammar, with a distinct palette, layout and typography '
              'approach; never reproduce specific source images or characters. '
              'Return {"angle":"...","viewer_promise":"...","keep":["..."],"change":["..."],'
              '"visual_style":{"description":"English own art direction","palette":["#..."],'
              '"composition":"English layout rules","shot_types":["wide","detail","diagram"]},'
              '"sections":[{"purpose":"...","focus":"...","fact_ids":["f1"]}]}. '
              'Reference JSON:\n' + json.dumps(data, ensure_ascii=False))
    known = {item['id'] for item in facts}
    def validate_blueprint(value):
        for key in ('angle', 'viewer_promise'):
            if not isinstance(value[key], str) or not 1 <= len(value[key]) <= 1800: raise ValueError('неверный формат '+key)
        if not _strings(value['keep']) or not _strings(value['change']): raise ValueError('keep/change: нужен непустой список редакторских решений')
        own = value['visual_style']
        if not isinstance(own['description'], str) or not own['description'].strip(): raise ValueError('visual_style.description: нет описания стиля')
        if not isinstance(own['composition'], str) or not _strings(own['palette']) or not _strings(own['shot_types']): raise ValueError('visual_style: неверная композиция, палитра или типы планов')
        if not saved_design and visual and _normal(own['description']) == _normal(visual.get('description', '')): raise ValueError('visual_style.description дословно повторяет исходник')
        sections = value['sections']
        if not isinstance(sections, list) or not 1 <= len(sections) <= target_sections: raise ValueError('sections: недопустимое количество разделов')
        used = set()
        for section in sections:
            if not isinstance(section['focus'], str) or not section['focus'].strip(): raise ValueError('sections.focus: нет темы раздела')
            if not isinstance(section['purpose'], str) or not section['purpose'].strip(): raise ValueError('sections.purpose: нет задачи раздела')
            if not _strings(section['fact_ids'], 1000) or not set(section['fact_ids']) <= known: raise ValueError('sections.fact_ids: отсутствующие или неизвестные утверждения')
            used.update(section['fact_ids'])
        if used != known: raise ValueError('sections.fact_ids: не все исходные утверждения включены в план')
    if progress: progress('собственный угол, структура и визуальная система')
    blueprint = _query(client, prompt, folder / 'blueprint.json', validate_blueprint, progress)
    selected = {item['id']: item['statement'] for item in facts}
    parts = []
    for i, section in enumerate(blueprint['sections']):
        netutil.check_cancel()
        if progress: progress(f'самостоятельный сценарий: часть {i + 1}/{len(blueprint["sections"])}')
        prompt = (BRIEF + narration_rules.writing('script',language,locale) + (rewrite.lang_rule(language) if language else 'Use the source language. ') +
                  'Write ONLY this section of a coherent original narration. No headings or production notes. '
                  'The first section establishes an honest hook; the last resolves the promised question. '
                  'Use original wording and explanatory reasoning, not a sentence-by-sentence paraphrase. '
                  'Only previous_output is continuity context, never repeat it. No new factual assertions '
                  'beyond the supplied claims. Hypothetical analogies must be clearly hypothetical. '
                  'Return {"script":"..."}. Reference JSON:\n' + json.dumps({
                      'language_reference': text[:600] if not language else '',
                      'angle': blueprint['angle'], 'viewer_promise': blueprint['viewer_promise'],'reference_presentation':presentation,
                      'structure': [{k: s[k] for k in ('purpose', 'focus')} for s in blueprint['sections']],
                      'section': i + 1, 'total': len(blueprint['sections']), 'focus': section['focus'],
                      'claims': {key: selected[key] for key in section['fact_ids']},
                      'target_words':None if narration_rules.language_code(language) in ('zh','ja') else max(20, round(targets / len(blueprint['sections']))),
                      'approximate_section_seconds':float((profile.get('sampling') or {}).get('duration',0))/len(blueprint['sections']),
                      'maximum_words': max(24, round(float(sample['source_seconds']) * 4)) if sample else None,
                      'previous_output': parts[-1][-900:] if parts else ''}, ensure_ascii=False))
        def validate_text(value):
            output = value['script']
            if not isinstance(output, str) or not output.strip(): raise ValueError('script: пустой текст')
            if narration_rules.wrong_script(output,language):raise ValueError('script: текст не соответствует выбранному языку')
            from faithful_rewrite import _issue
            if _issue(output, max(20, round(targets / len(blueprint['sections']))), language or ''): raise ValueError('script: текст слишком короткий для раздела')
            if sample and len(sb.tokens(output)) > float(sample['source_seconds']) * 10: raise ValueError('script: превышена длина для пробного отрезка')
            if sample and len(output.split()) > max(24, float(sample['source_seconds']) * 4): raise ValueError('script: превышено число слов для пробного отрезка')
            if overlap_report(text, output)['substantial_verbatim_overlap']: raise ValueError('script: слишком много дословных фрагментов исходника')
        value = _query(client, prompt, folder / f'script_{i:04d}.json', validate_text, progress)
        parts.append(value['script'].strip())
    result = '\n\n'.join(parts)
    if overlap_report(text, result)['substantial_verbatim_overlap']:
        raise ValueError('Сценарий содержит слишком много длинных дословных фрагментов исходника. Проверьте текст до озвучки.')
    own_profile = {'content_only': True, 'visual_style': copy.deepcopy(design or blueprint['visual_style']),
                   'montage': {'shots': []}, 'narrative_style': presentation['narrative_style'] or 'Independent reference-led explanatory narration.',
                   'sampling': copy.deepcopy(profile.get('sampling', {})),
                   'creative_direction': {'enhancement_level': 'original', 'editing_pace': cfg.get('editing_pace', 'balanced'),
                                          'visual_detail': cfg.get('visual_detail', 'rich')},
                   'adaptation': {key: copy.deepcopy(blueprint[key]) for key in ('angle', 'viewer_promise', 'keep', 'change')}}
    own_profile['adaptation']['facts_to_verify']=[{'claim':fact['statement'],'note':fact['review_note']} for fact in facts if fact.get('review_note','').strip()]
    own_profile['adaptation'].update(version=VERSION, writing_rules=narration_rules.VERSION, reference_presentation=presentation, source_claims=len(facts),
                                    lexical_review=overlap_report(text, result),
                                    claim_review='Утверждения перенесены из исходника; независимая проверка фактов не проводилась.')
    target=narration_rules.language_code(language)
    if target:own_profile['narration_language']=target
    for key in ('channel_design_ref', 'narration', 'sample','character_registry','reference_animation'):
        if key in profile: own_profile[key] = copy.deepcopy(profile[key])
    sb.atomic(folder / 'adaptation.json', {'profile': own_profile, 'script': result})
    return result, own_profile
