import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

import object_continuity as oc
import pipeline as p
import storyboard_cache as cache
from tests.test_location_image_recovery import ctx, isolate


def shots():
    return [{'index':i, 'text':text,'raw_prompt':text,'prompt':text,'duration':3,'characters':[], 'plan_id':str(i)}
            for i,text in enumerate(['Hero drives his blue car.', 'Another driver in a red van.',
                                     'The hero parks the same blue car.', 'The red van returns.', 'An abstract chart.'])]


def answer():
    return {'objects':[{'id':'hero_car','name':'Машина героя','description':'Blue five-door hatchback, round lamps, grey cabin.'},
                       {'id':'other_van','name':'Чужой фургон','description':'Red tall van, square lamps, black cabin.'}],
            'assignments':[{'index':i,'object_ids':ids} for i,ids in enumerate([
                ['hero_car'],['other_van'],['hero_car'],['other_van'],[]])]}


def test_one_story_car_across_rooms_and_distinct_other_vehicle_cached_without_tokens(tmp_path):
    llm=SimpleNamespace(chat_json=Mock(return_value=answer()))
    original=shots(); result=oc.prepare(original,{},llm,tmp_path,Mock())
    assert result[0]['recurring_objects']==result[2]['recurring_objects']
    assert result[1]['recurring_objects']==result[3]['recurring_objects']
    assert result[0]['recurring_objects']!=result[1]['recurring_objects']
    assert 'recurring_objects' not in result[4]
    assert result[4]['prompt']==original[4]['prompt']
    assert oc.prepare(original,{},llm,tmp_path,Mock())==result
    assert llm.chat_json.call_count==1
    assert 'recurring_objects' not in original[0]


def test_registry_cannot_change_car_between_batches(tmp_path):
    llm=SimpleNamespace(chat_json=Mock(side_effect=[
        {'objects':[answer()['objects'][0]],'assignments':[{'index':0,'object_ids':['hero_car']}]},
        {'objects':[],'assignments':[{'index':2,'object_ids':['hero_car']}]}]))
    result=oc.prepare([shots()[0],shots()[2]],{},llm,tmp_path,Mock(),batch_size=1)
    assert result[0]['recurring_objects']==result[1]['recurring_objects']
    request=json.loads(llm.chat_json.call_args.args[1].split('Reference JSON:\n')[1])
    assert request['registry']['hero_car']['description'].startswith('Blue')
    with pytest.raises(ValueError):oc.validate({'objects':[answer()['objects'][0]],'assignments':[]},[],request['registry'])


def test_reference_created_once_shared_across_styles_and_kept_after_restart(tmp_path):
    scenes=oc.prepare(shots(),{},SimpleNamespace(chat_json=Mock(return_value=answer())),tmp_path,Mock())
    def draw(scene,path,progress,refs):
        Image.new('RGB',(64,36),'blue' if 'Blue' in scene['prompt'] else 'red').save(path)
        return 'fixture'
    gen=Mock(side_effect=draw); context={'providers':['fastgen'],'image_gen':{'fastgen':gen}}
    oc.references(scenes,context,tmp_path,{},Mock())
    assert gen.call_count==2
    assert scenes[0]['object_reference_paths']==scenes[2]['object_reference_paths']
    assert scenes[0]['object_reference_signature']!=scenes[1]['object_reference_signature']
    oc.references(scenes,context,tmp_path,{},Mock())
    assert gen.call_count==2
    def draw_frame(scene,path,progress,refs):
        expected=scene.get('object_reference_paths') or {}
        assert all(ref in refs for ref in expected.values())
        Image.new('RGB',(64,36),'green').save(path);return 'fixture'
    p.generate_all_images(ctx(draw_frame),scenes,{},str(tmp_path),Mock(),{})
    state=json.loads((tmp_path/'images.json').read_text())
    assert state['0']['object_reference_signature']==scenes[0]['object_reference_signature']
    cache.publish(scenes,tmp_path,tmp_path/'assets')
    assert (tmp_path/'assets'/('i_'+cache.image_key(scenes[0])+'.png')).is_file()


def test_old_paid_frames_not_relabelled_as_new_identity_or_regenerated(tmp_path):
    scene={**shots()[0], 'object_reference_signature':'new-identity'}
    (tmp_path/'images').mkdir();path=tmp_path/'images/0000.png'
    Image.new('RGB',(64,36),'red').save(path);before=path.read_bytes()
    draw=Mock(side_effect=AssertionError('Existing paid frame must not be regenerated'))
    p.generate_all_images(ctx(draw),[scene],{},str(tmp_path),Mock(),{})
    cache.publish([scene],tmp_path,tmp_path/'assets')
    assert path.read_bytes()==before
    assert not list((tmp_path/'assets').glob('i_*.png'))


def test_qa_sees_object_reference_and_rejects_different_car_without_silent_best_pick(tmp_path):
    ref=tmp_path/'car.png';Image.new('RGB',(64,36),'blue').save(ref)
    scene={**shots()[0], 'object_reference_paths':{'hero_car':str(ref)},
           'recurring_objects':{'hero_car':{'description':'blue hatchback'}},'require_object_refs':True}
    attempts=[]
    def draw(sc,path,progress,refs):
        attempts.append(1);assert str(ref) in refs
        Image.new('RGB',(64,36),'red').save(path);return 'fixture'
    client=Mock();client.see_json.return_value={'ok':True,'identity_ok':False,'score':9,'issues':'red SUV instead of blue hatchback'}
    context=ctx(draw);context['qa']=client
    with pytest.raises(RuntimeError,match='референсом предмета'):
        p.generate_all_images(context,[scene],{},str(tmp_path),Mock(),{})
    assert len(attempts)==3
    assert not (tmp_path/'images/0000.png').exists()
    assert 'identity_ok' in client.see_json.call_args.args[0]
    assert 'blue hatchback' in client.see_json.call_args.args[1]


def test_fastgen_does_not_drop_object_reference_on_provider_rejection(tmp_path,monkeypatch):
    import threading
    import netutil
    monkeypatch.setattr(p,'HD_IMAGES',False)
    monkeypatch.setattr(netutil,'pause',lambda *a:None)
    ref=tmp_path/'car.png';Image.new('RGB',(32,18),'blue').save(ref)
    gate=SimpleNamespace(lock=threading.Lock(),sem=threading.Semaphore(1),disabled=False,
                         unavailable_ops={},hour_limit=0,acquire=Mock(),request_done=Mock())
    api=Mock();api.run.return_value={'status':'failed','error_code':'invalid_request','error':'reference invalid_argument'}
    result=p.gen_fastgen_image(api,gate,{'index':0,'prompt':'same car','require_object_refs':True},str(tmp_path/'out.png'),Mock(),[str(ref)],chain=[('first',3),('second',1)])
    assert result is None
    assert api.run.call_count==2
    assert all(call.args[0].get('inputs') for call in api.run.call_args_list)
