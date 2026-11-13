"""Room dependencies must survive transient errors without regenerating good frames."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image
import pytest

import netutil
import pipeline as p


@pytest.fixture(autouse=True)
def isolate(tmp_path,monkeypatch):
    p.CANCEL.clear();p.take_redo()
    monkeypatch.setattr(netutil,'CANCEL_CHECK',None)
    monkeypatch.setattr(p,'LOG_PATH',str(tmp_path/'log.txt'))
    monkeypatch.setattr(p,'warn_hours',lambda *a:None)
    yield
    p.CANCEL.clear();p.take_redo()


def scenes():
    return [{'index':i,'prompt':'A consistent room scene','characters':[],
             **({'location_id':'kitchen','location_anchor_index':0} if i in (0,1,3) else
                {'location_id':'hall','location_anchor_index':4} if i in (4,5) else {})} for i in range(6)]


def ctx(draw):
    return {'providers':['fastgen'],'bases':['fastgen'],'fallback':[],'api':None,
            'image_gen':{'fastgen':draw},'gates':{'fastgen':SimpleNamespace(threads=4)},'qa':None,
            'cfg':{'fastgen_api_key':'synthetic-secret'}}


def save(scene,path,refs):
    if scene.get('location_anchor_index') not in (None,scene['index']):
        assert Path(refs[0]).name==f'{scene["location_anchor_index"]:04d}.png'
        with Image.open(refs[0]) as anchor:assert anchor.getpixel((0,0))==(255,0,0)
    Image.new('RGB',(32,18),'red').save(path)
    return 'fixture-model'


def test_anchor_transient_failure_gets_second_pass_before_dependents(tmp_path):
    calls={};order=[];progress=Mock()
    def draw(scene,path,progress,refs):
        i=scene['index'];calls[i]=calls.get(i,0)+1;order.append(i)
        if i==0 and calls[i]==1:
            scene['_image_generation_error']='generation.failed'
            return None
        return save(scene,path,refs)
    p.generate_all_images(ctx(draw),scenes(),{},str(tmp_path),progress,{})
    assert calls=={0:2,1:1,2:1,3:1,4:1,5:1}
    assert order.index(1)>[j for j,i in enumerate(order) if i==0][-1]
    states=json.loads((tmp_path/'images.json').read_text())
    assert all(row['ok'] and not row.get('error') for row in states.values())
    values=[call.args[1] for call in progress.call_args_list]
    assert values==sorted(values) and min(values)>=0 and max(values)==6


def test_failed_dependent_does_not_block_later_shots_or_redraw_anchor(tmp_path):
    calls={};order=[]
    def draw(scene,path,progress,refs):
        i=scene['index'];calls[i]=calls.get(i,0)+1;order.append(i)
        if i==1 and calls[i]==1:return None
        return save(scene,path,refs)
    p.generate_all_images(ctx(draw),scenes(),{},str(tmp_path),Mock(),{})
    assert calls[0]==1 and calls[1]==2 and calls[3]==1
    assert order.index(3)<[j for j,i in enumerate(order) if i==1][-1]


def test_persistent_anchor_failure_preserves_provider_reason_and_other_rooms(tmp_path):
    calls={}
    def draw(scene,path,progress,refs):
        i=scene['index'];calls[i]=calls.get(i,0)+1
        if i==0:raise RuntimeError('generation.failed synthetic-secret')
        return save(scene,path,refs)
    with pytest.raises(RuntimeError,match='Не получен кадр локации.*generation.failed') as error:
        p.generate_all_images(ctx(draw),scenes(),{},str(tmp_path),Mock(),{})
    assert 'synthetic-secret' not in str(error.value)
    assert calls=={0:2,2:1,4:1,5:1}
    state=json.loads((tmp_path/'images.json').read_text())
    assert state['1']['blocked_by_anchor']==0 and state['3']['blocked_by_anchor']==0
    assert all(state[str(i)]['ok'] for i in (2,4,5))
    assert 'synthetic-secret' not in json.dumps(state)
    original={i:hashlib.sha256((tmp_path/'images'/f'{i:04d}.png').read_bytes()).hexdigest() for i in (2,4,5)}
    # Resume the exact same job: all successful frames remain byte-for-byte identical.
    resumed=[]
    def recover(scene,path,progress,refs):
        resumed.append(scene['index']);return save(scene,path,refs)
    p.generate_all_images(ctx(recover),scenes(),{},str(tmp_path),Mock(),{})
    assert resumed==[0,1,3]
    assert all(hashlib.sha256((tmp_path/'images'/f'{i:04d}.png').read_bytes()).hexdigest()==sha for i,sha in original.items())


def test_content_policy_is_not_retried_or_replaced_by_generic_room_error(tmp_path):
    calls=[]
    def draw(scene,path,progress,refs):
        calls.append(scene['index'])
        if scene['index']==0:raise p.SceneRejected('Сцена 1: generation.content_policy')
        return save(scene,path,refs)
    with pytest.raises(RuntimeError,match='Сцены 1:.*generation.content_policy'):
        p.generate_all_images(ctx(draw),scenes(),{},str(tmp_path),Mock(),{})
    assert calls.count(0)==1 and 1 not in calls and 3 not in calls
    assert all((tmp_path/'images'/f'{i:04d}.png').exists() for i in (2,4,5))


def test_explicit_anchor_redraw_is_ordered_and_preserves_other_rooms(tmp_path):
    calls={};queued=False
    def draw(scene,path,progress,refs):
        i=scene['index'];calls[i]=calls.get(i,0)+1
        return save(scene,path,refs)
    def complete(scene):
        nonlocal queued
        if scene['index']==0 and not queued:
            queued=True;p.request_redo(0)
    progress=Mock()
    p.generate_all_images(ctx(draw),scenes(),{},str(tmp_path),progress,{},on_image=complete)
    assert calls=={0:2,1:2,2:1,3:2,4:1,5:1}
    assert all(0<=call.args[1]<=6 for call in progress.call_args_list)


def test_existing_anchor_is_reused_when_only_one_room_child_is_missing(tmp_path):
    image_dir=tmp_path/'images';image_dir.mkdir()
    for i in (0,2,3,4,5):Image.new('RGB',(32,18),'red').save(image_dir/f'{i:04d}.png')
    p.write_json(str(tmp_path/'images.json'),{str(i):{'ok':True,'model':'cached'} for i in (0,2,3,4,5)})
    seen=[]
    def draw(scene,path,progress,refs):
        seen.append(scene['index']);return save(scene,path,refs)
    p.generate_all_images(ctx(draw),scenes(),{},str(tmp_path),Mock(),{})
    assert seen==[1]


def test_reported_success_without_file_is_retried_and_not_counted_ready(tmp_path):
    calls=0;progress=Mock()
    def draw(scene,path,report,refs):
        nonlocal calls
        calls+=1
        if calls==1:return 'fixture-model'
        Image.new('RGB',(32,18),'red').save(path)
        return 'fixture-model'
    p.generate_all_images(ctx(draw),[{'index':0,'prompt':'scene'}],{},str(tmp_path),progress,{})
    assert calls==2
    failed=[call for call in progress.call_args_list if 'файл картинки не получен' in str(call)]
    assert failed and failed[0].args[1]==0
    assert json.loads((tmp_path/'images.json').read_text())['0']['ok']
