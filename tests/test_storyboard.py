import copy
import json
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest
from PIL import Image

import app
import pipeline
import storyboard as sb
import storyboard_cache as cache


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'CONFIG_PATH', str(tmp_path/'config.json'))
    monkeypatch.setattr(app, 'data_dir', lambda:str(tmp_path))
    monkeypatch.setattr(pipeline, 'data_dir', lambda:str(tmp_path))
    monkeypatch.setattr(pipeline, 'LOG_PATH', str(tmp_path/'log.txt'))
    monkeypatch.setattr(app, 'STATE', {**app.STATE, 'running':False,'preparing':False,'queue_active':False,
                                    'storyboard_id':None,'source_profile':None,'source_profile_id':None,'log':[]})
    pipeline.CANCEL.clear()
    yield
    pipeline.CANCEL.clear()


@pytest.fixture
def profile(tmp_path):
    frames=[]
    for i,color in enumerate(['red','blue']):
        p=tmp_path/f'source{i}.jpg';Image.new('RGB',(80,45),color).save(p);frames.append(str(p))
    return {'visual_style':{'description':'cinematic painting','palette':['red','blue'],'composition':'wide','shot_types':['wide']},
            'narrative_style':'chronological','sampling':{'duration':10.},
            'montage':{'duration':10.,'shots':[{'index':i,'start':i*5.,'end':(i+1)*5.,'duration':5.,
                'description':desc,'redesign_prompt':desc,'motion':'static camera','reference_frame':frames[i],
                'transition_in':{'type':'cut','duration':0.}} for i,desc in enumerate(['A cat in a red room','A spaceship near a blue planet'])]}}


def board(profile):
    return sb.seal({'version':1,'script':'The spaceship arrives. The cat waits.', 'profile':profile,
        'rows':[{'id':f'plan-{i:05d}','narration':text,'source_shot':shot,'confidence':'high','reason':'same subject',
                 'prompt':prompt,'motion':'static camera','duration':None,'take':0}
                for i,(text,shot,prompt) in enumerate([('The spaceship arrives.',1,'New spaceship near blue planet'),('The cat waits.',0,'New cat in red room')])]})


def test_semantic_choice_can_reverse_source_order_and_resumes(profile,tmp_path):
    script='The spaceship arrives and approaches the planet. The cat waits patiently inside the room.'
    query=Mock(side_effect=lambda prompt:{'plans':[{'index':x['index'],'source_shot':1 if 'spaceship' in x['narration'] else 0,
                 'confidence':'high','reason':'subject','prompt':'New original scene','motion':'static camera'}
                 for x in json.loads(prompt.split('Reference JSON:\n')[1])['passages']]})
    created=sb.create(script,profile,query,tmp_path)
    assert [r['source_shot'] for r in created['rows']]==[1,0]
    assert query.call_count==1
    assert sb.create(script,profile,query,tmp_path)==created
    assert query.call_count==1


def test_rejects_model_hallucinated_shot(profile,tmp_path):
    with pytest.raises(sb.StoryboardError,match='неполную'):
        sb.create('Hello world.',profile,lambda p:{'plans':[{'index':0,'source_shot':100,'prompt':'test'}]},tmp_path)


def test_unmatched_is_explicit_not_positional(profile,tmp_path):
    result=sb.create('Entirely different event.',profile,lambda p:{'plans':[{'index':0,'source_shot':None,'confidence':'low','prompt':'New event'}]},tmp_path)
    assert result['rows'][0]['source_shot'] is None
    assert result['rows'][0]['confidence']=='low'


def test_edit_changes_script_and_reference_without_accepting_paths(profile):
    original=board(profile); rows=copy.deepcopy(original['rows'])
    rows[0].update(narration='A redesigned spaceship approaches.',source_shot=0,reference_frame='/etc/passwd')
    edited=sb.edit(original,rows)
    assert edited['id']!=original['id']
    assert edited['script'].startswith('A redesigned spaceship')
    assert 'reference_frame' not in edited['rows'][0]
    assert edited['rows'][0]['confidence']=='manual'
    assert original['rows'][0]['source_shot']==1

@pytest.mark.parametrize('duration',[float('nan'),float('inf'),-.5,0,31,'wrong'])
def test_duration_validation(profile,duration):
    original=board(profile); rows=copy.deepcopy(original['rows']);rows[0]['duration']=duration
    with pytest.raises(sb.StoryboardError):sb.edit(original,rows)


def test_missing_duplicate_rows_rejected(profile):
    original=board(profile)
    with pytest.raises(sb.StoryboardError):sb.edit(original,original['rows'][:1])
    with pytest.raises(sb.StoryboardError):sb.edit(original,[original['rows'][0]]*2)


def test_regenerate_invalidates_only_one_plan(profile):
    original=board(profile);changed=sb.regenerate(original,original['rows'][0]['id'])
    assert changed['rows'][0]['take']==1
    assert changed['rows'][1]==original['rows'][1]
    assert changed['id']!=original['id']


def words():
    return [{'text':t,'start':s,'end':e} for t,s,e in [('The',0.,.1),('spaceship',.2,.9),('arrives.',1.,1.9),
                                                    ('The',3.,3.2),('cat',3.3,4.),('waits.',4.1,5.9)]]


def test_speech_alignment_follows_words_not_equal_halves(profile):
    aligned,score=sb.align(board(profile),words(),6.)
    assert score==1.
    assert aligned[0]['end']==3.
    altered=words();altered[3].update(start=4.,end=4.1);altered[4].update(start=4.2,end=4.6);altered[5].update(start=4.7,end=5.9)
    aligned,score=sb.align(board(profile),altered,6.)
    assert aligned[0]['end']==4.
    assert aligned[1]['start']==4.


def test_unrelated_speech_rejected(profile):
    with pytest.raises(sb.StoryboardError,match='отличается'):
        sb.align(board(profile),[{'text':'unrelated','start':0.,'end':2.}],2.)


def test_token_alignment_ignores_punctuation_case(profile):
    altered=words();altered[1]['text']='SPACESHIP!'
    assert sb.align(board(profile),altered,6.)[1]==1.
    assert sb.tokens('猫、犬。')==['猫','犬']


def test_api_roundtrip_restart_and_busy_guard(profile):
    original=board(profile);app.store_storyboard(original)
    assert app.Api().get_storyboard()['board']['id']==original['id']
    app.STATE['storyboard_id']=None
    assert app.Api().get_storyboard()['board']['id']==original['id']
    app.STATE['running']=True
    assert not app.Api().save_storyboard(original['id'],original['rows'])['ok']
    public=app.Api().get_storyboard(original['id'])['board']
    assert 'profile' not in public
    assert '/source' not in json.dumps(public)


def test_corrupt_board_rejected(profile,tmp_path):
    original=board(profile);app.store_storyboard(original)
    path=tmp_path/'jobs/storyboards'/f'{original["id"]}.json'
    broken=json.loads(path.read_text());broken['script']='replaced';path.write_text(json.dumps(broken))
    assert not app.Api().get_storyboard(original['id'])['ok']


def test_preview_validation_before_paid_work(profile,monkeypatch):
    original=board(profile);app.store_storyboard(original)
    gen=Mock();monkeypatch.setattr(pipeline,'make_video',gen)
    for interval in [{'start':-1,'seconds':30},{'start':0,'seconds':31},{'start':float('nan'),'seconds':20}]:
        r=app.Api().start({'storyboard_id':original['id'],'script':original['script'],'preview':interval})
        assert not r['ok']
    gen.assert_not_called()


@pytest.fixture
def audio(tmp_path):
    p=tmp_path/'audio.wav'
    subprocess.run(['ffmpeg','-y','-loglevel','error','-f','lavfi','-i','sine=frequency=440:duration=6','-ar','16000',str(p)],check=True)
    return str(p)


def duration(path):
    return float(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',str(path)]))


def test_real_manual_timing_preserves_speech_sections(profile,audio,tmp_path):
    original=board(profile);original['rows'][0]['duration']=2.
    aligned,_=sb.align(original,words(),6.)
    output,timed,rows=sb.retime_audio(audio,words(),aligned,tmp_path)
    assert duration(output)==pytest.approx(5.,abs=.04)
    assert rows[0]['end']==2.
    assert next(w for w in timed if w['text']=='cat')['start']==pytest.approx(2.3)
    assert rows[-1]['end']==5.


def test_unreasonable_manual_speed_rejected(profile,audio,tmp_path):
    original=board(profile);original['rows'][0]['duration']=.5
    aligned,_=sb.align(original,words(),6.)
    with pytest.raises(sb.StoryboardError,match='вдвое'):sb.retime_audio(audio,words(),aligned,tmp_path)


def test_preview_selects_only_intersecting_scenes_and_captions(profile,audio,tmp_path):
    original=board(profile);aligned,_=sb.align(original,words(),6.)
    scenes=sb.scenes(original,aligned)
    chosen,assembly,cut,captions=sb.preview(scenes,audio,words(),4.,2.,tmp_path)
    assert [s['index'] for s in chosen]==[1]
    assert assembly[0]['preview_offset']==1.
    assert assembly[0]['full_duration']==3.
    assert assembly[0]['duration']==2.
    assert duration(cut)==pytest.approx(2.,abs=.02)
    assert captions[-1]['text']=='waits.'
    assert all(w['start']>=0 and w['end']<=2 for w in captions)


def test_plan_asset_cache_reuses_other_plan_after_edit(profile,tmp_path):
    original=board(profile);aligned,_=sb.align(original,words(),6.);scenes=sb.scenes(original,aligned)
    for scene in scenes:scene['prompt']=scene['source_redesign']
    first=tmp_path/'first';(first/'images').mkdir(parents=True)
    for scene in scenes:Image.new('RGB',(32,32),'red').save(first/'images'/f"{scene['index']:04d}.png")
    assets=tmp_path/'assets';cache.publish(scenes,first,assets)
    changed=copy.deepcopy(scenes);changed[0]['plan_take']=1
    second=tmp_path/'second';cache.restore(changed,second,assets)
    assert not (second/'images/0000.png').exists()
    assert (second/'images/0001.png').exists()


def test_preview_then_full_then_single_edit_reuses_completed_generations(profile,audio,tmp_path,monkeypatch):
    import shutil
    original=board(profile)
    job=tmp_path/'job';job.mkdir();pipeline.write_json(str(job/'source_profile.json'),profile)
    generated=tmp_path/'generated.mp4'
    subprocess.run(['ffmpeg','-y','-loglevel','error','-f','lavfi','-i','testsrc2=s=320x180:r=25:d=0.5',
                    '-c:v','libx264','-pix_fmt','yuv420p',str(generated)],check=True)
    api=Mock();api.usage.return_value={'capabilities':{'video.generate':True},'account_limits':{'video_generation_threads_allowed':1,'video_gen_per_hour_limit':100}}
    api.run.return_value={'status':'succeeded','results':[{}]}
    api.download_result.side_effect=lambda item,dest:shutil.copyfile(generated,dest)
    image_calls=[]
    def image(scene,path,*args):
        image_calls.append(scene['index']);Image.new('RGB',(320,180),'green').save(path);return 'fixture'
    ctx={'api':api,'providers':['fastgen'],'bases':['fastgen'],'fallback':[],
         'gates':{'fastgen':Mock(threads=1)},'image_gen':{'fastgen':image}}
    monkeypatch.setattr(pipeline,'W',320);monkeypatch.setattr(pipeline,'H',180)
    monkeypatch.setattr(pipeline,'load_config',lambda:{'fastgen_api_key':'fake','quality':'speed'})
    monkeypatch.setattr(pipeline,'resolve_source',lambda *a,**kw:(audio,str(job),'test'))
    monkeypatch.setattr(pipeline,'transcribe',lambda *a,**kw:{'text':original['script'],'words':words()})
    monkeypatch.setattr(pipeline,'make_context',lambda *a:ctx)
    monkeypatch.setattr(pipeline,'make_text_engine',lambda *a:('fastgen',api,'fastgen',api))
    def forbidden(*a,**kw):raise AssertionError('An edited storyboard must not be overwritten by another LLM prompt pass')
    monkeypatch.setattr(pipeline.LLM,'chat_json',forbidden)
    kwargs={'audio_path':audio,'script_text':original['script'],'provider':'fastgen','out_dir':str(tmp_path/'output'),'storyboard':original}
    preview_path=pipeline.make_video(**kwargs,preview_range={'start':3.2,'seconds':2.})
    assert Path(preview_path).parent.name=='previews'
    assert duration(preview_path)==pytest.approx(2.,abs=.15)
    assert image_calls==[1] and api.run.call_count==1
    full_path=pipeline.make_video(**kwargs)
    assert duration(full_path)==pytest.approx(6.,abs=.15)
    assert sorted(image_calls)==[0,1] and api.run.call_count==2
    assert Path(preview_path).exists() and full_path!=preview_path
    changed=sb.regenerate(original,'plan-00000')
    kwargs['storyboard']=changed
    pipeline.make_video(**kwargs)
    assert sorted(image_calls)==[0,0,1] and api.run.call_count==3
    assert Path(full_path).exists()


def test_changed_script_rejected_before_tts(profile,monkeypatch):
    paid=Mock();monkeypatch.setattr(pipeline,'resolve_source',paid)
    monkeypatch.setattr(pipeline,'load_config',lambda:{})
    with pytest.raises(sb.StoryboardError,match='изменился'):
        pipeline.make_video(script_text='Other narration',storyboard=board(profile))
    paid.assert_not_called()


def test_missing_reference_rejected_before_paid_tts(profile,monkeypatch):
    original=board(profile)
    Path(profile['montage']['shots'][0]['reference_frame']).unlink()
    paid=Mock();monkeypatch.setattr(pipeline,'resolve_source',paid)
    monkeypatch.setattr(pipeline,'load_config',lambda:{})
    with pytest.raises(sb.StoryboardError,match='Референсы'):
        pipeline.make_video(script_text=original['script'],storyboard=original)
    paid.assert_not_called()


def test_semantic_planning_cancellation_keeps_completed_batches(profile,tmp_path):
    calls=[]
    script=' '.join(f'Sentence {i} describes a separate visual event today.' for i in range(20))
    def query(prompt):
        units=json.loads(prompt.split('Reference JSON:\n')[1])['passages'];calls.append(units)
        if len(calls)==2:raise pipeline.Cancelled('Stopped')
        return {'plans':[{'index':u['index'],'source_shot':None,'confidence':'low','prompt':'New scene'} for u in units]}
    with pytest.raises(pipeline.Cancelled):sb.create(script,profile,query,tmp_path)
    assert len(list(tmp_path.glob('*/batch_00000.json')))==1
    assert not list(tmp_path.glob('*/batch_00008.json'))


def test_successful_publish_replaces_corrupt_cached_image(profile,tmp_path):
    original=board(profile);aligned,_=sb.align(original,words(),6.);scenes=sb.scenes(original,aligned)
    scenes[0]['prompt']='new cat';scene=scenes[:1]
    job=tmp_path/'job';(job/'images').mkdir(parents=True)
    Image.new('RGB',(32,32),'red').save(job/'images/0000.png')
    assets=tmp_path/'assets';assets.mkdir()
    broken=assets/('i_'+cache.image_key(scene[0])+'.png');broken.write_bytes(b'broken')
    cache.publish(scene,job,assets)
    with Image.open(broken) as image:assert image.size==(32,32)
