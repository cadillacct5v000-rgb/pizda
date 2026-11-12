import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

import storyboard as sb
import studio


def profile():
    return studio.with_direction({'visual_style':{'description':'minimal ink'},'montage':{'shots':[
        {'index':0,'duration':3,'description':'A person explains heat.','redesign_prompt':'Ink person in heat'}]}},{})


def script(n=12):
    return ' '.join(f'Person {i} explains heat in the desert.' for i in range(n))


def data(prompt):
    return json.JSONDecoder().raw_decode(prompt.split('Reference JSON:\n')[1])[0]


def plan(index):
    return {'index':index,'source_shot':0,'confidence':'high','prompt':f'Original visual {index}',
            'motion':'slow hand gesture','editorial_role':'explain','shot_size':'detail','overlay_text':''}


def test_only_bad_plan_is_corrected_without_changing_valid_core(tmp_path):
    prompts=[]
    def query(prompt):
        prompts.append(prompt);units=data(prompt)['passages']
        if 'Correct the schema' in prompt:
            assert [u['index'] for u in units]==[3]
            assert data(prompt)['source_shots']==[]
            value=plan(3);value.update(prompt='UNWANTED replacement',source_shot=None,motion='UNWANTED motion')
            return {'plans':[value]}
        result=[plan(u['index']) for u in units]
        for p in result:
            if p['index']==3:p.pop('shot_size');p.pop('editorial_role')
        return {'plans':result}
    board=sb.create(script(8),profile(),query,tmp_path)
    assert len(prompts)==2
    assert 'CREATIVE PLAN SCHEMA' in prompts[0]
    assert all(r['prompt']==f'Original visual {i}' for i,r in enumerate(board['rows']))
    assert board['rows'][3]['motion']=='slow hand gesture' and board['rows'][3]['source_shot']==0
    assert sb.create(script(8),profile(),query,tmp_path)==board and len(prompts)==2


def test_failed_correction_preserves_partial_and_retry_only_missing(tmp_path):
    calls=[]
    def broken(prompt):
        units=data(prompt)['passages'];calls.append([u['index'] for u in units])
        result=[plan(u['index']) for u in units]
        for p in result:
            if p['index'] in (2,5):p.pop('shot_size')
        return {'plans':result}
    with pytest.raises(sb.StoryboardError,match='shot_size'):
        sb.create(script(8),profile(),broken,tmp_path)
    assert calls==[list(range(8)),[2,5]]
    saved=json.loads(next(tmp_path.glob('*/batch_00000.partial.json')).read_text())
    assert len(saved['result']['plans'])==6
    assert next(tmp_path.glob('*/batch_00000.received.json')).is_file()
    retry=[]
    def fixed(prompt):
        assert 'Correct the schema' in prompt
        units=data(prompt)['passages'];retry.append([u['index'] for u in units])
        return {'plans':[plan(u['index']) for u in units]}
    board=sb.create(script(8),profile(),fixed,tmp_path)
    assert len(board['rows'])==8 and retry==[[2,5]]


def test_core_missing_repair_rejects_unknown_refs_and_saves_good_plans(tmp_path):
    def query(prompt):
        values=[plan(u['index']) for u in data(prompt)['passages']]
        values[0]['source_shot']=999
        return {'plans':values}
    with pytest.raises(sb.StoryboardError,match='source_shot'):
        sb.create(script(4),profile(),query,tmp_path)
    saved=json.loads(next(tmp_path.glob('*/batch_00000.partial.json')).read_text())
    assert {p['index'] for p in saved['result']['plans']}=={1,2,3}


def test_completed_batches_reuse_legacy_prompt_cache_without_calls(tmp_path):
    # Create the current cache, then rewrite signatures to exactly what 2.0.0 used.
    captured=[]
    def answer(prompt):
        captured.append(prompt)
        return {'plans':[plan(u['index']) for u in data(prompt)['passages']]}
    text=script(24);old=sb.create(text,profile(),answer,tmp_path)
    assert len(captured)>1
    caches=sorted(p for p in tmp_path.glob('*/batch_*.json') if p.stem.removeprefix('batch_').isdigit())
    for path,prompt in zip(caches,captured):
        # Undo the two precise, schema-only prompt additions.
        legacy=prompt.replace('"motion":"English motion","editorial_role":"explain","shot_size":"medium","overlay_text":""}]}', '"motion":"English motion"}]}',1)
        begin=legacy.index('\nCREATIVE PLAN SCHEMA:')
        end=legacy.index('\nReference JSON:\n',begin)
        legacy=legacy[:begin]+legacy[end:]
        value=json.loads(path.read_text());value['signature']=sb.digest(legacy);sb.atomic(path,value)
    never=Mock(side_effect=AssertionError('Valid 2.0 drafts must not be repaid'))
    assert sb.create(text,profile(),never,tmp_path)==old
    never.assert_not_called()


def test_optional_null_overlay_and_enum_casing_are_local_not_paid_fixes(tmp_path):
    def answer(prompt):
        return {'plans':[{**plan(u['index']),'editorial_role':' Explain ','shot_size':'DETAIL','overlay_text':None} for u in data(prompt)['passages']]}
    query=Mock(side_effect=answer)
    board=sb.create(script(3),profile(),query,tmp_path)
    assert query.call_count==1
    assert all(r['overlay_text']=='' and r['shot_size']=='detail' for r in board['rows'])


def test_200_failed_raw_answer_can_be_recovered_without_resending_source(tmp_path):
    text=script(4);cfg=profile();seen=[]
    def broken(prompt):
        seen.append(prompt)
        return {'plans':[{k:v for k,v in plan(u['index']).items() if k!='shot_size'} for u in data(prompt)['passages']]}
    with pytest.raises(sb.StoryboardError):sb.create(text,cfg,broken,tmp_path)
    folder=next(tmp_path.iterdir())
    raw=json.loads((folder/'batch_00000.received.json').read_text())
    for p in folder.glob('*'):p.unlink()
    sb.atomic(folder/'batch_00000.reply.json',{'signature':raw['signature'],'kind':'model','complete':False,'text':json.dumps(raw['result'])})
    calls=[]
    def fixed(prompt):
        calls.append(prompt);assert 'Correct the schema' in prompt
        assert data(prompt)['source_shots']==[]
        return {'plans':[plan(u['index']) for u in data(prompt)['passages']]}
    result=sb.create(text,cfg,fixed,tmp_path)
    assert len(result['rows'])==4 and len(calls)==1
