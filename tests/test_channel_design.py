import copy
import hashlib
import io
from pathlib import Path
import shutil
import subprocess
from unittest.mock import Mock

from PIL import Image
import pytest

import app
import channel_design as d
from native_projects import ProjectStore
import pipeline as p
import storyboard_cache as cache
import subtitles


def value():
    return {'name':'Channel','description':'Flat minimal whiteboard drawings over photographic rainy backgrounds.',
            'palette':['#FFFFFF','#C00000'],'faces':'blank','font':'Arial','layout':'minimal','rules':'Keep the approved silhouette.'}


def asset(tmp_path,color='red',transparent=True):
    path=tmp_path/(color+'.png');im=Image.new('RGBA' if transparent else 'RGB',(20,40),(0,0,0,0) if transparent else 'white')
    from PIL import ImageDraw
    ImageDraw.Draw(im).rectangle((2,2,17,37),fill=color)
    im.save(path);return path


def test_designs_survive_restart_source_changes_and_saved_project_versions(tmp_path):
    store=d.DesignStore(tmp_path);first=store.save(value())
    project=store.store.save('First',{'channel_design_id':first['id'],'channel_design_revision':first['revision']})
    second=store.save({**first,'description':'Different rendering'},first['id'],first['revision'])
    restored=d.DesignStore(tmp_path)
    assert restored.get(first['id'],1)['description']==first['description']
    assert restored.get(first['id'])['description']==second['description']
    assert restored.store.get(project['id'])['options']['channel_design_revision']==1
    assert restored.list()[0]['revision']==2
    with pytest.raises(ValueError):store.save(first,first['id'],1)
    with pytest.raises(ValueError):store.get('../escape')


def test_profile_applies_same_approved_design_to_new_source_and_uses_exact_asset_bytes(tmp_path):
    store=d.DesignStore(tmp_path);profile=store.save(value());image=asset(tmp_path)
    raw=image.read_bytes();profile=store.import_asset(profile['id'],1,image)
    copied=store.asset_path(profile['assets'][0]);assert copied.read_bytes()==raw
    original=[{'index':0,'prompt':'A scene','raw_prompt':'white background infographic'},
              {'index':1,'prompt':'Another source in realistic style','raw_prompt':'driver in car'}]
    scenes=d.apply(original,profile,tmp_path)
    assert all(s['channel_references']==[str(copied)] for s in scenes)
    assert scenes[0]['channel_design_signature']==scenes[1]['channel_design_signature']
    assert all('plain featureless faces' in s['prompt'] for s in scenes)
    assert 'channel_design' not in original[0]
    assert store.import_asset(profile['id'],profile['revision'],image)['revision']==profile['revision']
    copied.write_bytes(b'changed')
    with pytest.raises(ValueError,match='отсутствует или изменён'):d.apply(original,profile,tmp_path)


def test_host_and_font_changes_do_not_repay_backgrounds_or_video(tmp_path):
    store=d.DesignStore(tmp_path);profile=store.save(value());host=asset(tmp_path)
    profile=store.import_asset(profile['id'],1,host,'host')
    renamed={**profile,'name':'Renamed','font':'Intro Demo Black CAPS'}
    other={**profile,'assets':[{**a,'sha256':'a'*64,'file':'a'*64+'.png'} for a in profile['assets']]}
    assert d.generation_fingerprint(profile)==d.generation_fingerprint(renamed)==d.generation_fingerprint(other)
    assert d.fingerprint(profile)!=d.fingerprint(renamed)
    a,_=p.visual_job_dir(str(tmp_path/'job'),'sk','quality',True,False,channel_design=profile)
    b,_=p.visual_job_dir(str(tmp_path/'job'),'sk','quality',True,False,channel_design=renamed)
    assert a==b
    changed={**profile,'faces':'detailed'}
    c,_=p.visual_job_dir(str(tmp_path/'job'),'sk','quality',True,False,channel_design=changed)
    assert c!=a
    scenes=d.apply([{'index':0,'prompt':'A','raw_prompt':'diagram'},{'index':1,'prompt':'B','raw_prompt':'road at night'}],profile,tmp_path)
    assert len(scenes[0]['channel_overlays'])==1 and not scenes[1]['channel_overlays']
    assert 'composited separately' in scenes[0]['prompt']


def test_invalid_overlay_does_not_create_new_version_and_resume_is_frozen(tmp_path,monkeypatch):
    store=d.DesignStore(tmp_path);profile=store.save(value())
    with pytest.raises(ValueError,match='прозрачным'):store.import_asset(profile['id'],1,asset(tmp_path,transparent=False),'host')
    assert store.get(profile['id'])['revision']==1
    monkeypatch.setattr(app,'data_dir',lambda:str(tmp_path))
    monkeypatch.setattr(app,'load_cfg',lambda:{'channel_design_id':profile['id'],'channel_design_revision':1})
    frozen=app.selected_channel_design({})
    store.save({**profile,'faces':'detailed'},profile['id'],1)
    assert frozen['faces']=='blank'
    assert app.selected_channel_design({'channel_design_id':profile['id'],'channel_design_revision':1})==frozen
    assert app.selected_channel_design({'channel_design_id':''}) is None
    assert p.video_prompt({'motion':'x','raw_prompt':'y','style_line':'z'})=='x. y z '+p.NO_TEXT


def test_channel_font_used_for_programmatic_labels(tmp_path):
    out=tmp_path/'captions.ass'
    profile={'channel_design':{'font':'Intro Demo Black CAPS'},'visual_style':{'palette':['#C00000']}}
    subtitles.write_ass([{'text':'Test','start':0.,'end':1.}],out,profile=profile)
    assert 'Style: Caption,Intro Demo Black CAPS,' in out.read_text(encoding='utf-8')
    assert 'Style: Label,Intro Demo Black CAPS,' in out.read_text(encoding='utf-8')


@pytest.mark.skipif(not shutil.which('ffmpeg'),reason='FFmpeg required')
def test_approved_host_is_fixed_after_moving_video_and_survives_cache(tmp_path):
    p.CANCEL.clear();store=d.DesignStore(tmp_path);profile=store.save(value());host=asset(tmp_path)
    profile=store.import_asset(profile['id'],1,host,'host')
    scene=d.apply([{'index':0,'prompt':'x','raw_prompt':'diagram'}],profile,tmp_path)[0]
    movie=tmp_path/'source.mp4'
    subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','testsrc2=s=320x180:r=25:d=0.6','-threads','2','-c:v','libx264','-pix_fmt','yuv420p',str(movie)],check=True)
    rendered=d.decorate_clip(scene,str(movie),tmp_path,320,180,25)
    for t in (0.04,0.4):
        raw=subprocess.check_output(['ffmpeg','-v','error','-ss',str(t),'-i',rendered,'-frames:v','1','-threads','1','-f','image2pipe','-vcodec','png','-'])
        im=Image.open(io.BytesIO(raw));r,g,b=im.getpixel((30,130))
        assert r>235 and g<20 and b<20  # unchanged red artwork, despite moving background
    before=Path(rendered).stat().st_mtime_ns
    assert d.decorate_clip(scene,str(movie),tmp_path,320,180,25)==rendered
    assert Path(rendered).stat().st_mtime_ns==before
    assert store.asset_path(profile['assets'][0]).read_bytes()==host.read_bytes()


def test_unavailable_design_check_reuses_paid_image_and_blocks_video_until_approved(tmp_path):
    import json
    from tests.test_location_image_recovery import ctx
    p.CANCEL.clear();profile=d.DesignStore(tmp_path).save(value())
    scene=d.apply([{'index':0,'prompt':'scene','raw_prompt':'diagram','characters':[]}],profile,tmp_path)[0]
    def draw(s,path,progress,refs):Image.new('RGB',(64,36),'blue').save(path);return 'paid-fixture'
    generator=Mock(side_effect=draw);client=Mock()
    client.see_json.side_effect=[RuntimeError('QA temporarily down'),{'ok':True,'score':9,'issues':''}]
    context=ctx(generator);context['qa']=client;callback=Mock()
    with pytest.raises(p.DesignCheckUnavailable):p.generate_all_images(context,[scene],{},str(tmp_path),Mock(),{},on_image=callback)
    image=tmp_path/'images/0000.png';before=image.read_bytes()
    callback.assert_not_called();assert generator.call_count==1
    state=json.loads((tmp_path/'images.json').read_text())['0']
    assert state['design_check_pending'] and not state['ok']
    p.drop_failed_images([scene],str(tmp_path));assert image.read_bytes()==before
    p.generate_all_images(context,[scene],{},str(tmp_path),Mock(),{},on_image=callback)
    assert generator.call_count==1 and client.see_json.call_count==2
    assert image.read_bytes()==before and callback.call_count==1
    assert json.loads((tmp_path/'images.json').read_text())['0']['design_approved'] is True


def test_rejected_design_is_not_published_as_best_bad_image_or_rebilled_in_second_pass(tmp_path):
    from tests.test_location_image_recovery import ctx
    p.CANCEL.clear();profile=d.DesignStore(tmp_path).save(value())
    scene=d.apply([{'index':0,'prompt':'scene','raw_prompt':'diagram','characters':[]}],profile,tmp_path)[0]
    def draw(s,path,progress,refs):Image.new('RGB',(64,36),'red').save(path);return 'fixture'
    generator=Mock(side_effect=draw);client=Mock();client.see_json.return_value={'ok':False,'score':9,'issues':'detailed face violates blank-face channel design'}
    context=ctx(generator);context['qa']=client
    with pytest.raises(RuntimeError,match='дизайна канала'):
        p.generate_all_images(context,[scene],{},str(tmp_path),Mock(),{})
    assert generator.call_count==3
    assert not (tmp_path/'images/0000.png').exists()
    assert len(list((tmp_path/'design_rejected').glob('*.png')))==3


def test_api_captures_source_design_and_reference_files_locally(tmp_path,monkeypatch):
    images=[asset(tmp_path,color,transparent=False) for color in ['red','blue','green']]
    original={'visual_style':{'description':'Original flat channel style','palette':['#FFFFFF']},
              'montage':{'shots':[{'reference_frame':str(image)} for image in images]}}
    monkeypatch.setattr(app,'STATE',{**copy.deepcopy(app.STATE),'source_profile':original,'running':False,'preparing':False,'queue_active':False})
    monkeypatch.setattr(app,'data_dir',lambda:str(tmp_path))
    cfg={};monkeypatch.setattr(app,'load_cfg',lambda:dict(cfg));monkeypatch.setattr(app,'save_cfg',lambda value:cfg.update(value))
    result=app.Api().channel_design_capture('Saved source')
    assert result['ok'] and len(result['profile']['assets'])==3
    assert cfg['channel_design_id']==result['profile']['id']
    assert app.STATE['source_profile']==original
    store=d.DesignStore(tmp_path)
    assert {store.asset_path(a).read_bytes() for a in result['profile']['assets']}=={i.read_bytes() for i in images}


def test_artwork_replacement_and_removal_keep_old_project_design_and_portable_paths(tmp_path):
    root=tmp_path/'original';store=d.DesignStore(root);profile=store.save(value())
    first=store.import_asset(profile['id'],1,asset(tmp_path,'red'),'host')
    second=store.import_asset(profile['id'],first['revision'],asset(tmp_path,'blue'),'host')
    assert len(second['assets'])==1 and first['assets'][0]['sha256']!=second['assets'][0]['sha256']
    assert d.generation_fingerprint(first)==d.generation_fingerprint(second)
    third=store.remove_asset(profile['id'],second['revision'],0)
    assert not third['assets']
    assert store.asset_path(store.get(first['id'],first['revision'])['assets'][0]).is_file()
    target=tmp_path/'moved';target.mkdir()
    shutil.copy2(root/'projects.sqlite',target/'projects.sqlite');shutil.copytree(root/'jobs',target/'jobs')
    moved=d.DesignStore(target)
    assert moved.asset_path(moved.get(first['id'],first['revision'])['assets'][0]).read_bytes()==asset(tmp_path,'red').read_bytes()


def test_local_design_never_requests_generated_videos_and_preflight_reports_zero(tmp_path,monkeypatch):
    store=d.DesignStore(tmp_path);profile=store.save(value())
    scenes=d.apply([{'index':0,'prompt':'x','raw_prompt':'diagram'},{'index':1,'prompt':'y','raw_prompt':'driver'}],profile,tmp_path)
    assert p.animated_scenes(scenes,'video',150)==[]
    assert p.animated_scenes(scenes,'mixed',10)==[]
    permitted=d.apply(scenes,{**profile,'motion_policy':'generated'},tmp_path)
    assert len(p.animated_scenes(permitted,'video'))==2
    assert d.generation_fingerprint(profile)==d.generation_fingerprint({**profile,'motion_policy':'generated'})
    monkeypatch.setattr(app,'data_dir',lambda:str(tmp_path))
    monkeypatch.setattr(app,'load_cfg',lambda:{'video_engine':'elysium','channel_design_id':profile['id'],'channel_design_revision':1})
    monkeypatch.setattr(app,'estimate_plan',lambda *a,**kw:{'clips':80})
    result=app.production_check({'providers':['fastgen'],'mode':'video'})
    assert result['ok'] and result['estimate']['clips']==0
    assert result['video_access']=='not_needed'


from tests.test_pipeline_integration import offline_workflow


@pytest.mark.skipif(not shutil.which('ffmpeg'),reason='FFmpeg required')
def test_full_local_channel_render_preserves_logo_audio_and_makes_zero_video_requests(offline_workflow,tmp_path):
    import json
    wf=offline_workflow;profile=d.DesignStore(wf.data).save(value())
    profile=d.DesignStore(wf.data).import_asset(profile['id'],1,asset(tmp_path),'logo')
    wf.control['duration']=1.2
    output=p.make_video(wf.audio(1.2),provider='google',mode='video',out_dir=str(tmp_path/'out'),channel_design=profile)
    assert wf.calls['video']==0 and wf.calls['image']==1
    data=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',output]))
    assert {s['codec_type'] for s in data['streams']}=={'video','audio'}
    assert float(data['format']['duration'])==pytest.approx(1.2,abs=.12)
    png=subprocess.check_output(['ffmpeg','-v','error','-i',output,'-frames:v','1','-threads','1','-f','image2pipe','-vcodec','png','-'])
    red,green,blue=Image.open(io.BytesIO(png)).getpixel((297,34))
    assert red>225 and green<35 and blue<35
