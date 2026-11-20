import copy
import hashlib
from pathlib import Path
from unittest.mock import Mock

from PIL import Image
import pytest

import channel_design as channels
import drawing_plugins as drawing
import pipeline
import storyboard_cache


def design(style='sketchbook'):
    return {'name':'Our channel','description':'Original faceless educational drawings.','faces':'blank','palette':['#167b88','#9ddbdd','#dcad57'],
            'drawing':{'enabled':True,'style':style,'seed':24,'motion':'still','symbols':[]}}


@pytest.mark.parametrize('value',[{'style':'untrusted.py'},{'style':'sketchbook','seed':True},{'style':'lineart','seed':-1},
                                  {'style':'lineart','enabled':'yes'},{'style':'lineart','motion':'eval'}])
def test_invalid_plugin_settings_are_rejected(value):
    with pytest.raises(ValueError):drawing.settings(value)


@pytest.mark.parametrize('field,value',[('x',float('nan')),('w',True),('h',-1),('x',1.5)])
def test_geometry_is_bounded_data(field,value):
    scene=drawing.example();scene['objects'][0][field]=value
    with pytest.raises(ValueError):drawing.validate_scene(scene)


def test_code_and_unknown_symbols_are_not_executable_inputs():
    with pytest.raises(ValueError):drawing.validate_scene({'code':'import os; os.system("bad")'})
    with pytest.raises(ValueError):drawing.validate_scene({'objects':[{'symbol':'s_0000000000000000','x':0,'y':0,'w':.5,'h':.5}]},drawing.settings({'style':'sketchbook'}))


@pytest.mark.parametrize('style',['sketchbook','lineart'])
def test_real_brushes_are_deterministic_and_cache_exact_image(tmp_path,monkeypatch,style):
    a=tmp_path/'a.png';b=tmp_path/'b.png';scene=drawing.example();d=design(style)
    drawing.render(scene,d,a,320,180);drawing.render(scene,d,b,320,180)
    assert a.read_bytes()==b.read_bytes()
    assert drawing.metadata(a)['scene']==scene
    with Image.open(a) as image:assert image.size==(320,180) and image.getextrema()[0][0]<100
    before=a.stat().st_mtime_ns
    d['drawing']['motion']='draw';drawing.render(scene,d,a,320,180)
    assert a.stat().st_mtime_ns==before and drawing.metadata(a)['drawing']['motion']=='draw'
    a.write_bytes(b'changed')
    assert drawing.metadata(a) is None


def test_all_brushes_reuse_one_cached_scene_and_ignore_new_model(tmp_path,monkeypatch):
    client=Mock();client.chat_json.return_value=drawing.example();d=design()
    scene=drawing.plan(client,'One original whale illustration',d,tmp_path)
    d['drawing']['style']='lineart';d['drawing']['seed']=42
    assert drawing.plan(client,'One original whale illustration',d,tmp_path)==scene
    assert client.chat_json.call_count==1
    monkeypatch.setattr(drawing,'browser_path',lambda:None)
    rows=drawing.gallery(scene,d,tmp_path/'previews')
    assert sum(bool(row.get('image')) for row in rows)==2
    assert not rows[-1]['available'] and 'Edge' in rows[-1]['reason']
    assert client.chat_json.call_count==1


def test_pinned_geometry_survives_new_videos_and_profile_revisions(tmp_path):
    d=design();scene=drawing.example();d['drawing']=drawing.pin_object(d['drawing'],scene,0,'Willy',['наш кит','Willy','whale'])
    store=channels.DesignStore(tmp_path);first=store.save(d)
    second=store.save({**first,'description':'Another background'},first['id'],1)
    original=store.get(first['id'],1)['drawing']['symbols'][0]
    assert original==store.get(first['id'],2)['drawing']['symbols'][0]
    assert original['shapes']==scene['objects'][0]['shapes']
    assert drawing.matched_symbols(first['drawing'],'A whale meets a boat')[0]['id']==original['id']
    assert not drawing.matched_symbols(first['drawing'],'Whalebone and Китай are unrelated')
    new=drawing.validate_scene({'objects':[{'symbol':original['id'],'x':.1,'y':.1,'w':.4,'h':.8}]},second['drawing'])
    assert new['objects'][0]['w']/new['objects'][0]['h']==pytest.approx(original['aspect'])


def test_required_pinned_object_cannot_be_silently_redrawn(tmp_path):
    d=design();d['drawing']=drawing.pin_object(d['drawing'],drawing.example(),0,'Willy',['whale'])
    client=Mock();client.chat_json.return_value=drawing.example()
    with pytest.raises(ValueError,match='не использовала закреплённый объект'):
        drawing.plan(client,'Our whale is swimming',d,tmp_path)
    assert list(tmp_path.glob('*.received.json'))
    assert not list(tmp_path.glob('scene_*.json')) or all('received' in p.name or 'reply' in p.name for p in tmp_path.glob('scene_*.json'))


def test_duplicate_object_aliases_are_rejected():
    d=drawing.pin_object(design()['drawing'],drawing.example(),0,'Willy',['whale'])
    other=copy.deepcopy(d['symbols'][0]);other['id']='s_1111111111111111';d['symbols'].append(other)
    with pytest.raises(ValueError,match='Обозначение уже'):drawing.settings(d)


def test_motion_changes_keep_image_identity_but_style_and_symbol_changes_do_not():
    d=design();first=channels.generation_fingerprint(d)
    d['drawing']['motion']='draw';assert channels.generation_fingerprint(d)==first
    d['drawing']['style']='lineart';assert channels.generation_fingerprint(d)!=first
    d['drawing']['enabled']=False
    assert channels.generation_fingerprint(d)==channels.generation_fingerprint({k:v for k,v in d.items() if k!='drawing'})


def test_storyboard_cache_preserves_bound_geometry_sidecar(tmp_path):
    job=tmp_path/'job';image=job/'images/0000.png';drawing.render(drawing.example(),design(),image,320,180)
    scenes=[{'index':0,'plan_id':'plan','prompt':'Own whale','duration':1}]
    cache=tmp_path/'cache';storyboard_cache.publish(scenes,job,cache)
    image.unlink();Path(str(image)+'.drawing.json').unlink()
    storyboard_cache.restore(scenes,job,cache)
    assert drawing.metadata(image)['scene']==drawing.example()
    other=tmp_path/'other.png';Image.new('RGB',(320,180),'red').save(other)
    assert not drawing.restore_metadata(other,image)
    assert not Path(str(other)+'.drawing.json').exists()


def test_local_image_context_needs_no_image_api_key_and_has_no_paid_fallback(tmp_path,monkeypatch):
    import studio
    client=Mock();client.chat_json.return_value=drawing.example()
    monkeypatch.setattr(studio,'text_client',lambda cfg:client)
    monkeypatch.setattr(pipeline,'data_dir',lambda:str(tmp_path))
    monkeypatch.setattr(pipeline,'FastGen',Mock(side_effect=AssertionError('No image provider')))
    ctx=pipeline.make_context({'_channel_design':design(),'llm_engine':'anthropic','anthropic_api_key':'fixture'},['fastgen'])
    assert ctx['providers']==['drawing'] and ctx['fallback']==[] and not ctx['api']
    scenes=[{'index':0,'text':'A whale','raw_prompt':'A whale','prompt':'A whale','duration':1}]
    pipeline.CANCEL.clear();pipeline.generate_all_images(ctx,scenes,{},str(tmp_path),lambda *a:None,{})
    assert drawing.metadata(tmp_path/'images/0000.png')
    assert client.chat_json.call_count==1
    pipeline.generate_all_images(ctx,scenes,{},str(tmp_path),lambda *a:None,{})
    assert client.chat_json.call_count==1


def test_plugin_preflight_skips_unused_image_key_but_checks_text_key(monkeypatch):
    import app
    cfg={'llm_engine':'anthropic','anthropic_model':'claude-sonnet-5-5','anthropic_api_key':'fixture',
         'stt_engine':'assemblyai','assemblyai_api_key':'fixture','qa_images':False,'video_engine':'providers'}
    monkeypatch.setattr(app,'load_cfg',lambda:copy.deepcopy(cfg))
    monkeypatch.setattr(app,'selected_channel_design',lambda *a:design())
    assert app.Api()._validate_common(['fastgen'],{'animation':{'strategy':'local','limit':0}}) is None
    cfg['anthropic_api_key']=''
    assert 'Claude' in app.Api()._validate_common(['fastgen'],{'animation':{'strategy':'local','limit':0}})


def test_real_drawing_clip_is_playable_and_holds_final_picture(tmp_path,monkeypatch):
    from media_tools import run_media
    image=tmp_path/'drawing.png';d=design();d['drawing']['motion']='draw'
    drawing.render(drawing.example(),d,image,320,180)
    out=tmp_path/'draw.mp4';pipeline.CANCEL.clear()
    drawing.render_clip(image,1,out,320,180,10,pipeline.ffmpeg_bin(),pipeline.check_cancel)
    assert storyboard_cache.valid(out,'video')
    result=run_media([pipeline.ffmpeg_bin(),'-v','error','-sseof','-0.1','-i',str(out),'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-'])
    final=Image.frombytes('RGB',(320,180),result.stdout)
    import numpy as np
    delta=np.abs(np.asarray(final).astype(float)-np.asarray(Image.open(image)).astype(float))
    assert delta.mean()<8


def test_assembly_uses_new_effect_without_repainting(tmp_path,monkeypatch):
    image=tmp_path/'images/0000.png';drawing.render(drawing.example(),design(),image,320,180)
    d=design();d['drawing']['motion']='draw'
    class Checked(Exception):pass
    def check(*args):
        assert drawing.metadata(image)['drawing']['motion']=='draw'
        raise Checked()
    monkeypatch.setattr(pipeline,'render_scene_clip',check)
    with pytest.raises(Checked):pipeline.assemble_video([{'index':0,'duration':1,'channel_design':d}],str(image.parent),{},'unused',str(tmp_path),'unused.mp4',lambda *a:None)


def test_gallery_example_and_local_motion_do_not_call_text_model(tmp_path,monkeypatch):
    import app
    import studio
    monkeypatch.setattr(app,'data_dir',lambda:str(tmp_path))
    monkeypatch.setattr(studio,'text_client',Mock(side_effect=AssertionError('No model for example')))
    monkeypatch.setattr(drawing,'browser_path',lambda:None)
    app.STATE.update(running=False,preparing=False,queue_active=False,service_switch=False)
    result=app.Api().drawing_variants(design(),'',True)
    assert result['ok'] and result['example'] and len(result['choices'])==3
    motion=app.Api().drawing_motion_sample(design(),result['scene'])
    assert motion['ok'] and motion['video'].startswith('data:video/mp4;base64,')
    studio.text_client.assert_not_called()
    assert not app.STATE['preparing']


@pytest.mark.skipif(not __import__('os').environ.get('KADROGEN_DRAWING_TEST_BROWSER'),reason='Explicit real headless browser required')
def test_real_sketchling_adapter_renders_without_node_or_network(tmp_path,monkeypatch):
    monkeypatch.setenv('KADROGEN_DRAWING_BROWSER',__import__('os').environ['KADROGEN_DRAWING_TEST_BROWSER'])
    image=tmp_path/'sketchling.png';drawing.render(drawing.example(),design('sketchling'),image,320,180)
    assert drawing.metadata(image)
    with Image.open(image) as result:assert result.size==(320,180) and result.getextrema()[0][0]<100
    bundle=Path(drawing.__file__).parent/'assets/drawing/sketchling.js'
    assert 'GreenSock' not in bundle.read_text() and 'gsap.com' not in bundle.read_text()


def test_full_video_uses_chosen_plugin_and_reuses_ready_scene(tmp_path,monkeypatch):
    import studio
    from media_tools import run_media
    audio=tmp_path/'voice.wav'
    result=run_media([pipeline.ffmpeg_bin(),'-y','-v','error','-f','lavfi','-i','sine=frequency=440:duration=2',str(audio)])
    assert result.returncode==0
    job=tmp_path/'job';job.mkdir()
    monkeypatch.setattr(pipeline,'data_dir',lambda:str(tmp_path))
    monkeypatch.setattr(pipeline,'resolve_source',lambda *a,**k:(str(audio),str(job),'drawing-test'))
    monkeypatch.setattr(pipeline,'transcribe',lambda *a,**k:{'text':'A whale swims.', 'words':[
        {'text':'A','start':0,'end':.3},{'text':'whale','start':.3,'end':1.1},{'text':'swims.','start':1.1,'end':2}]})
    unused=Mock();unused.model='fixture';unused.chat_json.side_effect=AssertionError('No extra prompt, identity or location calls')
    monkeypatch.setattr(pipeline,'make_text_engine',lambda cfg:('assemblyai',None,'anthropic',unused))
    client=Mock();client.chat_json.return_value=drawing.example()
    monkeypatch.setattr(studio,'text_client',lambda *a,**k:client)
    monkeypatch.setattr(pipeline,'FastGen',Mock(side_effect=AssertionError('No paid image API')))
    monkeypatch.setattr(pipeline,'W',320);monkeypatch.setattr(pipeline,'H',180)
    d=design();d['drawing']['motion']='draw'
    cfg={'llm_engine':'anthropic','anthropic_api_key':'fixture','stt_engine':'assemblyai','assemblyai_api_key':'fixture',
         'image_providers':['fastgen'],'qa_images':False,'object_continuity':True,'location_continuity':True}
    args=dict(audio_path=str(audio),out_dir=str(tmp_path/'output'),provider=['fastgen'],channel_design=d,animation={'strategy':'local','limit':0},render_fps=25,config_snapshot=cfg,use_refs=True)
    first=pipeline.make_video(**args)
    assert storyboard_cache.valid(Path(first),'video') and client.chat_json.call_count==1
    assert list(job.glob('visual_*/images/*.png.drawing.json'))
    assert pipeline.make_video(**args)==first
    assert client.chat_json.call_count==1
    unused.chat_json.assert_not_called()
