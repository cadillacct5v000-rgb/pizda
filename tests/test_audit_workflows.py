"""Cross-feature regressions found in the 2.0.19 audit; all services are offline."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

import app
import animation_selection as selection
import channel_design as design
import pipeline
import studio
from tests.test_channel_design import value
from tests.test_pipeline_integration import offline_workflow


@pytest.fixture
def local(tmp_path,monkeypatch):
    monkeypatch.setattr(app,'data_dir',lambda:str(tmp_path))
    monkeypatch.setattr(app,'CONFIG_PATH',str(tmp_path/'config.json'))
    monkeypatch.setattr(app,'STATE',{**copy.deepcopy(app.STATE),'running':False,'preparing':False,'queue_active':False,
                                   'source_profile':None,'source_profile_id':None,'storyboard_id':None,'log':[]})
    pipeline.CANCEL.clear()
    yield tmp_path
    pipeline.CANCEL.clear()


def test_channel_design_guide_does_not_exclude_every_shot_from_automatic_animation(tmp_path):
    profile=design.DesignStore(tmp_path).save(value())
    rows=[{'index':0,'prompt':'A calm road','raw_prompt':'A calm road','editorial_role':'hook','shot_size':'wide'},
          {'index':1,'prompt':'Wheel diagram','raw_prompt':'Wheel diagram','shot_size':'diagram'}]
    styled=design.apply(rows,profile,tmp_path)
    assert 'diagram' in styled[0]['prompt']
    result=selection.select(styled,{'strategy':'key','limit':2})
    assert result['count']==1 and result['rows'][0]['selected'] and not result['rows'][1]['selected']


def test_channel_source_can_be_estimated_and_directed_through_the_actual_api(local,monkeypatch):
    profile=design.DesignStore(local).save(value());content=design.content_profile(profile)
    app.save_cfg({'fastgen_api_key':'fixture','stt_engine':'fastgen','llm_engine':'fastgen','video_engine':'elysium',
                  'channel_design_id':profile['id'],'channel_design_revision':1})
    app.STATE.update(source_profile=content,source_profile_id='channel-source')
    opts={'source':'script','script':'A road. A car stops. '*20,'source_profile_id':'channel-source','providers':['fastgen'],
          'mode':'mixed','animation':{'strategy':'key','limit':2}}
    estimate=app.estimate_plan(opts,limits=False)
    assert estimate['ok'] and estimate['clips']==2
    worker=Mock();monkeypatch.setattr(app.threading,'Thread',worker)
    result=app.Api().studio_action('board',opts)
    assert result['ok'],result
    worker.return_value.start.assert_called_once()
    assert worker.call_args.kwargs['args'][3]['content_only']


@pytest.mark.parametrize('animation,mode,first,needs_video',[
    ({'strategy':'legacy','limit':10},'images',0,False),
    ({'strategy':'legacy','limit':10},'images',1,True),
    ({'strategy':'legacy','limit':10},'video',0,True),
    ({'strategy':'local','limit':10},'video',0,False),
    ({'strategy':'key','limit':0},'mixed',0,False),
    ({'strategy':'key','limit':2},'mixed',0,True)])
def test_images_and_zero_budget_do_not_require_an_unused_video_key(local,animation,mode,first,needs_video):
    app.save_cfg({'fastgen_api_key':'fixture','stt_engine':'fastgen','llm_engine':'fastgen','video_engine':'elysium'})
    error=app.Api()._validate_common(['fastgen'],{'animation':animation,'mode':mode,'video_first':first})
    assert bool(error)==needs_video
    if error:assert 'Elysium' in error


def test_settings_are_frozen_for_a_running_job_and_manual_video_retry(local,monkeypatch):
    supplied={'render_fps':60,'elysium_video_model':'x-ai/grok-imagine-video','marker':['original']}
    captured=[]
    def render(*args,**kwargs):captured.append(kwargs);return str(local/'video.mp4')
    monkeypatch.setattr(app.pipeline,'make_video',render)
    monkeypatch.setattr(app,'apply_engine',lambda *a:None);monkeypatch.setattr(app,'keep_awake',lambda *a:None)
    app.run_job(None,['fastgen'],'images','ultrareal',False,script='Fixed script',config_snapshot=supplied,render_fps=60)
    supplied['marker'].append('changed')
    assert captured[0]['config_snapshot']['marker']==['original']
    assert app._VIDEO_RESUME[1][-1]['marker']==['original']


def test_queue_carries_frozen_fps_and_configuration_to_each_job(local,monkeypatch):
    monkeypatch.setattr(app,'QUEUE',[]);monkeypatch.setattr(app,'QUEUE_STOP',False)
    app.Api().queue_add({'source':'script','script':'One queued item','tts':'edge'})
    calls=[]
    def job(*args,**kwargs):calls.append(kwargs);app.STATE.update(cancelled=False,error=None,result='video.mp4')
    monkeypatch.setattr(app,'run_job',job)
    monkeypatch.setattr(app,'keep_awake',lambda *a:None)
    common={'provider':['fastgen'],'mode':'images','style':'ultrareal','use_refs':False,'subtitles':False,
            'motion':False,'tts':'edge','video_first':0,'hd':False,'render_fps':60,'config_snapshot':{'marker':'frozen'}}
    app.run_queue(common)
    assert calls[0]['render_fps']==60 and calls[0]['config_snapshot']=={'marker':'frozen'}


def test_gallery_marks_only_selected_videos_in_the_actual_elysium_model_directory(local):
    job=local/'job';(job/'images').mkdir(parents=True);identity='123456abcdef'
    video_dir=job/('videos_'+identity);video_dir.mkdir()
    for i in (0,1):
        Image.new('RGB',(64,36),'white').save(job/'images'/f'{i:04d}.png')
        (video_dir/f'{i:04d}.mp4').write_bytes(b'fixture; gallery checks file presence only')
    (job/'video_display.json').write_text(json.dumps({'directory':video_dir.name,'selected':[0]}))
    app.STATE.update(job_dir=str(job),scenes=2)
    result=app.Api().gallery([])
    assert [r['video'] for r in result['items']]==[True,False]
    assert len(app.Api().gallery([r['key'] for r in result['items']])['items'])==0


def test_real_channel_render_selects_a_clip_and_reuses_media_on_restart(offline_workflow,tmp_path):
    wf=offline_workflow;profile=design.DesignStore(wf.data).save(value())
    params={'provider':'google','mode':'mixed','out_dir':str(tmp_path/'out'),'channel_design':profile,
            'animation':{'strategy':'key','limit':1}}
    audio=wf.audio(1.2)
    out=pipeline.make_video(audio,**params)
    assert Path(out).is_file() and wf.calls['video']==1
    pipeline.make_video(audio,**params)
    assert wf.calls['image']==1 and wf.calls['video']==1


def test_selected_video_failure_blocks_silent_picture_substitution_and_keeps_images(offline_workflow,tmp_path,monkeypatch):
    wf=offline_workflow
    generator=Mock(return_value={});monkeypatch.setattr(pipeline,'generate_all_videos',generator)
    with pytest.raises(RuntimeError,match='видеовставки'):
        pipeline.make_video(wf.audio(1.2),provider='google',mode='mixed',out_dir=str(tmp_path/'out'),
                            animation={'strategy':'key','limit':1})
    assert wf.calls['image']==1 and not list((tmp_path/'out').glob('*.mp4'))
    assert list(wf.data.glob('jobs/**/images/0000.png'))


def test_corrupt_settings_restore_keys_and_design_from_last_successful_save(local):
    import config_store
    path=local/'config.json';saved={'fastgen_api_key':'fixture-secret','channel_design_id':'a'*32,'channel_design_revision':3}
    config_store.write(path,saved)
    path.write_text('{incomplete',encoding='utf-8')
    assert app.load_cfg()==saved
    app.Api().save_config({'lang':'en'})
    assert app.load_cfg()['fastgen_api_key']=='fixture-secret'
    assert app.load_cfg()['channel_design_revision']==3
    assert json.loads(path.read_text())['lang']=='en'


def test_corrupt_settings_without_backup_are_never_replaced_with_empty_defaults(local):
    path=local/'config.json';path.write_text('{incomplete',encoding='utf-8')
    with pytest.raises(ValueError,match='резервную'):app.Api().save_config({'lang':'en'})
    assert path.read_text()=='{incomplete'


def test_overlay_clip_hashing_streams_the_file_instead_of_loading_entire_video(tmp_path,monkeypatch):
    # No rendering necessary on a cache hit, but the identity must use actual clip bytes.
    clip=tmp_path/'clip.mp4';clip.write_bytes(b'fixture-clip')
    original=Path.read_bytes
    def reject_clip_read(path):
        if path==clip:raise AssertionError('Whole clip must not be loaded into RAM')
        return original(path)
    monkeypatch.setattr(Path,'read_bytes',reject_clip_read)
    digest=design.digest([__import__('hashlib').sha256(b'fixture-clip').hexdigest(),[['sha','logo']],320,180,25,'fixed-overlays-v1'])
    expected=tmp_path/'channel_layers'/(digest+'.mp4');expected.parent.mkdir();expected.write_bytes(b'cached')
    monkeypatch.setattr('media_tools.valid_video',lambda p:True)
    assert design.decorate_clip({'channel_overlays':[{'sha256':'sha','role':'logo'}]},clip,tmp_path,320,180,25)==str(expected)


def test_damaged_local_render_is_rebuilt_without_new_generation(offline_workflow,tmp_path,monkeypatch):
    from media_tools import valid_video
    wf=offline_workflow;audio=wf.audio(1.2)
    monkeypatch.setattr(pipeline,'cleanup_job',lambda *a:None)  # Retain an interrupted job's local intermediates.
    output=pipeline.make_video(audio,provider='google',mode='images',out_dir=str(tmp_path/'out'))
    clips=list(wf.data.glob('jobs/**/clips/0000.mp4'));assert len(clips)==1
    clips[0].write_bytes(b'<html>not a video</html>')
    assert not valid_video(clips[0])
    pipeline.make_video(audio,provider='google',mode='images',out_dir=str(tmp_path/'out'))
    assert wf.calls['image']==1 and wf.calls['video']==0 and valid_video(clips[0])
    assert Path(output).is_file()
