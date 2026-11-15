"""Offline Elysium replacement contracts: no real credentials or billable calls."""
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import threading
import time
from unittest.mock import Mock

import pytest
import requests
from PIL import Image

import app
import elysium_media as media
import elysium_replacement as routing
import netutil
import pipeline
import source_analysis
import studio
from native_projects import ProjectStore
from tests.test_pipeline_integration import offline_workflow

_REAL_TEXT_ENGINE=pipeline.make_text_engine
_REAL_CONTEXT=pipeline.make_context


def response(body,status=200):
    r=requests.Response();r.status_code=status;r.url='https://elysium.example/v1/api'
    r.headers['Content-Type']='application/json';r._content=json.dumps(body).encode();r._content_consumed=True
    return r


def gemini(parts):return {'candidates':[{'finishReason':'STOP','content':{'parts':parts}}]}


def image_response():
    buf=io.BytesIO();Image.new('RGB',(64,36),'green').save(buf,format='PNG')
    return response(gemini([{'inlineData':{'mimeType':'image/png','data':base64.b64encode(buf.getvalue()).decode()}}]))


def config():
    return {'elysium_replace_fastgen':True,'elysium_api_key':'fixture-elysium-secret','fastgen_api_key':'fixture-old-fastgen',
            'llm_engine':'fastgen','stt_engine':'fastgen','source_analysis_engine':'fastgen','rewrite_engine':'fastgen',
            'tts_service':'fastgen','elysium_model':'anthropic/claude-sonnet-5.5-provider-4'}


@pytest.fixture(autouse=True)
def isolate(tmp_path,monkeypatch):
    monkeypatch.setattr(media,'data_dir',lambda:str(tmp_path))
    monkeypatch.setattr(app,'data_dir',lambda:str(tmp_path));monkeypatch.setattr(app,'CONFIG_PATH',str(tmp_path/'config.json'))
    monkeypatch.setattr(app,'STATE',{**copy.deepcopy(app.STATE),'running':False,'preparing':False,'queue_active':False,
        'cancelled':False,'error':None,'service_switch':None,'queue':None,'log':[]})
    monkeypatch.setattr(app,'_SERVICE_RESUME',None);monkeypatch.setattr(app,'_VIDEO_RESUME',None)
    pipeline.CANCEL.clear()
    yield
    pipeline.CANCEL.clear()


def client(tmp_path,posts=None):
    session=Mock();session.headers={};session.post=Mock(side_effect=posts) if posts else Mock(return_value=image_response())
    return media.MediaClient('fixture-elysium-secret','https://elysium.example/v1',store=ProjectStore(tmp_path),session=session,poll=0),session


def test_mapping_covers_all_fastgen_stages_without_mutating_credentials():
    raw=config();raw['image_keys']={'fastgen':['old-key']};original=copy.deepcopy(raw)
    actual=routing.effective(raw)
    assert raw==original and actual['fastgen_api_key']=='' and actual['image_keys']['fastgen']==[]
    assert all(actual[k]=='elysium' for k in ('llm_engine','stt_engine','rewrite_engine','source_analysis_engine','video_engine'))
    assert actual['source_analysis_model']=='google/gemini-3.6-flash'
    assert routing.service(actual,'fastgen')=='elysium'
    assert routing.service(actual,'elevenlabs')=='elevenlabs'


def test_clients_never_construct_fastgen_when_replacement_is_selected(tmp_path,monkeypatch):
    blocked=Mock(side_effect=AssertionError('No FastGen access permitted'))
    monkeypatch.setattr(pipeline,'FastGen',blocked)
    monkeypatch.setattr(pipeline,'OpenAIClient',Mock())
    engine,stt,text_engine,text=pipeline.make_text_engine(config())
    assert engine==text_engine=='elysium' and isinstance(stt,media.ElysiumTranscriber)
    assert source_analysis._provider(config())[:2]==('elysium','fixture-elysium-secret')
    assert pipeline.stt_fallbacks('elysium')==[]
    ctx=pipeline.make_context(config(),['fastgen'])
    assert ctx['bases']==['elysium'] and ctx['providers']==['elysium'] and ctx['api'] is None
    assert ctx['video_engine']=='elysium'
    blocked.assert_not_called()


def test_native_gemini_image_preserves_references_and_reuses_received_result(tmp_path):
    c,s=client(tmp_path);ref=tmp_path/'reference.png';Image.new('RGB',(32,32),'red').save(ref)
    scene={'index':0,'plan_id':'same-plan','plan_take':0,'prompt':'Preserve the exact approved car.'}
    a=tmp_path/'one.png';b=tmp_path/'other-visual-directory.png'
    assert c.image(scene,a,Mock(),[str(ref)],routing.IMAGE_MODELS[0]).startswith('elysium:')
    assert c.image(scene,b,Mock(),[str(ref)],routing.IMAGE_MODELS[0]).startswith('elysium:')
    assert a.read_bytes()==b.read_bytes() and s.post.call_count==1
    payload=s.post.call_args.kwargs['json'];assert 'image' in payload['generationConfig']['responseModalities'][1].lower()
    assert payload['generationConfig']['imageConfig']['aspectRatio']=='16:9'
    assert payload['contents'][0]['parts'][1]['inlineData']['mimeType']=='image/png'
    assert 'compatible/v1beta/models/google/gemini-' in s.post.call_args.args[0]


def test_uncertain_image_does_not_duplicate_after_directory_change_or_restart(tmp_path):
    c,s=client(tmp_path,[requests.ReadTimeout('fixture-elysium-secret')])
    scene={'index':0,'plan_id':'plan','prompt':'A car','plan_take':0}
    with pytest.raises(media.ElysiumMediaError) as error:c.image(scene,tmp_path/'one.png',Mock(),[],routing.IMAGE_MODELS[0])
    assert error.value.retry_operation and 'fixture-elysium-secret' not in str(error.value)
    again=media.MediaClient(c.key,c.base,store=ProjectStore(tmp_path),session=s)
    with pytest.raises(media.ElysiumMediaError):again.image(scene,tmp_path/'two.png',Mock(),[],routing.IMAGE_MODELS[0])
    assert s.post.call_count==1
    c.store.authorize_video_retry(error.value.retry_operation)
    s.post.side_effect=None;s.post.return_value=image_response()
    again.image(scene,tmp_path/'three.png',Mock(),[],routing.IMAGE_MODELS[0])
    assert s.post.call_count==2


def test_received_image_survives_cancellation_without_new_post(tmp_path,monkeypatch):
    c,s=client(tmp_path)
    def post(*a,**kw):pipeline.CANCEL.set();return image_response()
    s.post.side_effect=post;monkeypatch.setattr(netutil,'CANCEL_CHECK',pipeline.check_cancel)
    scene={'index':0,'prompt':'A car'}
    with pytest.raises(pipeline.Cancelled):c.image(scene,tmp_path/'one.png',Mock(),[],routing.IMAGE_MODELS[0])
    pipeline.CANCEL.clear();c.image(scene,tmp_path/'one.png',Mock(),[],routing.IMAGE_MODELS[0])
    assert s.post.call_count==1


def test_provider_refusal_stops_and_redacts_key(tmp_path):
    c,s=client(tmp_path,[response({'error':{'message':'Denied fixture-elysium-secret'}},403)])
    with pytest.raises(media.ElysiumMediaError) as error:c.image({'index':0,'prompt':'Car'},tmp_path/'one.png',Mock(),[],routing.IMAGE_MODELS[0])
    assert not error.value.retry_operation and 'fixture-elysium-secret' not in str(error.value)
    assert s.post.call_count==1


def test_elysium_asr_validates_actual_word_timing_and_never_invents_missing_timestamps(tmp_path):
    import subprocess
    path=tmp_path/'source.wav'
    subprocess.run([pipeline.ffmpeg_bin(),'-y','-v','error','-f','lavfi','-i','sine=frequency=440:duration=1',str(path)],check=True)
    c,s=client(tmp_path,[response(gemini([{'text':json.dumps({'words':[{'text':'Привет','start':.1,'end':.8}]})}]))])
    stt=media.ElysiumTranscriber(c.key,client=c)
    result=stt.transcribe(path,duration=1)
    assert result['words']==[{'text':'Привет','start':.1,'end':.8}]
    assert s.post.call_args.kwargs['json']['contents'][0]['parts'][1]['inlineData']['mimeType']=='audio/mpeg'
    other=tmp_path/'other.wav';other.write_bytes(path.read_bytes())
    stt.model='google/gemini-3.5-flash';s.post.side_effect=[response(gemini([{'text':'{"words":[{"text":"Bad","start":0.9,"end":0.1}]}'}]))]
    with pytest.raises(media.ElysiumMediaError,match='таймкоды'):stt.transcribe(other,duration=1)


def test_sonnet_preset_applies_to_every_text_entry_point_and_keeps_media_models(tmp_path,monkeypatch):
    app.save_cfg(config());preset=app.Api().use_sonnet()
    assert preset['ok']
    cfg=app.load_cfg();assert cfg['llm_engine']==cfg['rewrite_engine']=='elysium'
    assert cfg['elysium_model']=='anthropic/claude-sonnet-5.5-provider-4'
    assert routing.effective(cfg)['source_analysis_model'].startswith('google/gemini-')
    client=studio.text_client(cfg)
    assert client.engine=='elysium' and client.model==preset['model']
    app.save_cfg({**cfg,'anthropic_api_key':'fixture-direct-anthropic'})
    assert app.Api().use_sonnet('anthropic')['ok']
    assert app.load_cfg()['llm_engine']==app.load_cfg()['rewrite_engine']=='anthropic'


def test_ready_fastgen_voice_is_reused_without_calling_either_provider(tmp_path,monkeypatch):
    import subprocess
    monkeypatch.setattr(pipeline,'data_dir',lambda:str(tmp_path))
    cfg=routing.effective(config());text='Привет.'
    folder,base=pipeline.script_job_dir(text)
    identity=json.dumps({'service':'fastgen','voice':'Kore','source_profile':None},sort_keys=True,ensure_ascii=False)
    folder=Path(tmp_path,'jobs',base+'_'+hashlib.sha256(identity.encode()).hexdigest()[:12]);folder.mkdir(parents=True)
    voice=folder/'voiceover.mp3'
    subprocess.run([pipeline.ffmpeg_bin(),'-y','-v','error','-f','lavfi','-i','sine=duration=1','-b:a','192k',str(voice)],check=True)
    (folder/'voiceover.mp3.json').write_text(json.dumps({'text_sha256':hashlib.sha256(text.encode()).hexdigest(),'service':'fastgen','voice':'Kore'}))
    monkeypatch.setattr(pipeline,'make_tts',Mock(side_effect=AssertionError('Ready voice must not be synthesized again')))
    output,job,_=pipeline.resolve_source(cfg,None,text,'fastgen','Kore',Mock())
    assert output==str(voice) and job==str(folder)


def test_live_switch_drains_old_worker_and_preserves_frozen_job_parameters(tmp_path,monkeypatch):
    app.save_cfg(config());entered=threading.Event();resumed=threading.Event();seen=[]
    @app.resumable_service_task
    def run_job(config_snapshot=None):
        if config_snapshot.get('elysium_replace_fastgen'):
            seen.append(config_snapshot);app.STATE.update(running=False);resumed.set();return
        entered.set();pipeline.CANCEL.wait(3);app.STATE.update(running=False)
    app.STATE['running']=True
    old=threading.Thread(target=run_job,kwargs={'config_snapshot':{'marker':'keep-this','elysium_replace_fastgen':False}})
    old.start();assert entered.wait(2)
    result=app.Api().switch_to_elysium({'elysium_model':'anthropic/claude-sonnet-5.5-provider-4'})
    assert result['ok'] and result['resuming'];assert resumed.wait(3)
    old.join(2);assert not old.is_alive()
    assert seen[0]['marker']=='keep-this' and seen[0]['elysium_replace_fastgen']
    assert seen[0]['elysium_model']=='anthropic/claude-sonnet-5.5-provider-4'


def test_settings_and_project_options_preserve_credentials_and_pin_replacement(tmp_path):
    app.save_cfg(config());app.Api().save_config({'elysium_replace_fastgen':True})
    assert app.load_cfg()['fastgen_api_key']=='fixture-old-fastgen'
    from native_projects import safe_options
    opts=safe_options({'elysium_replace_fastgen':True,'elysium_model':'anthropic/claude-sonnet-5.5-provider-4'})
    assert opts['elysium_replace_fastgen'] and opts['elysium_model'].startswith('anthropic/claude-sonnet')
    assert not any('key' in k for k in opts)


def test_tts_uses_catalog_speakers_and_async_ids_without_rebilling_or_leaking_auth(tmp_path,monkeypatch):
    import subprocess
    audio=tmp_path/'fixture.mp3'
    subprocess.run([pipeline.ffmpeg_bin(),'-y','-v','error','-f','lavfi','-i','sine=duration=0.5','-b:a','192k',str(audio)],check=True)
    c,s=client(tmp_path);created=[]
    def post(url,**kwargs):
        created.append(kwargs['json']);return response({'id':'task_'+str(len(created)),'status':'queued'})
    def get(url,**kwargs):
        assert url.startswith(c.base), 'Provider key must never reach the external audio CDN'
        if url.endswith('/media/models'):
            return response({'data':[{'id':'google/gemini-3-8-flash-tts','operations':[{'name':'generate','input':{
                'properties':{'speakers':{'items':{'properties':{'voice_name':{'enum':['Charon','Kore']}}}}}}}]}]})
        return response({'id':url.rsplit('/',1)[-1],'status':'completed','tracks':[{'url':'https://cdn.example/speech.mp3'}]})
    def download(url,**kwargs):
        assert 'headers' not in kwargs and 'auth' not in kwargs
        r=response({});r.headers['Content-Type']='audio/mpeg';r._content=audio.read_bytes();return r
    s.post.side_effect=post;s.get.side_effect=get;monkeypatch.setattr(media.requests,'get',download)
    tts=media.ElysiumTTS(c.key,client=c);out=tmp_path/'speech.mp3'
    text='A complete sentence. '*210
    tts.synthesize(text,'Kore',out)
    assert out.stat().st_size>1000 and len(created)==2
    for payload in created:
        assert payload['input']['speakers']==[{'speaker_id':'narrator','voice_name':'Kore','accent':'Neutral','pace':'Natural'}]
        assert payload['input']['dialogue_turns'][0]['text']
    tts.synthesize(text,'Kore',out)
    assert len(created)==2


def test_complete_render_reuses_fastgen_image_then_fills_missing_via_elysium_only(offline_workflow,tmp_path,monkeypatch):
    wf=offline_workflow;audio=wf.audio(1.2)
    # First run represents a completed pre-replacement FastGen job.
    first=pipeline.make_video(audio,provider='fastgen',mode='images',out_dir=str(tmp_path/'out'))
    old_frames=list(wf.data.glob('jobs/**/images/0000.png'));assert len(old_frames)==1
    original=old_frames[0].read_bytes()
    cfg={**config(),'object_continuity':False,'tts_service':'edge','image_providers':['fastgen']}
    monkeypatch.setattr(pipeline,'load_config',lambda:cfg)
    monkeypatch.setattr(pipeline,'make_context',_REAL_CONTEXT)
    monkeypatch.setattr(pipeline,'make_text_engine',_REAL_TEXT_ENGINE)
    monkeypatch.setattr(media,'data_dir',lambda:str(wf.data))
    blocked=Mock(side_effect=AssertionError('FastGen must never be called'))
    monkeypatch.setattr(pipeline,'FastGen',blocked)
    posts=[]
    def http(self,method,url,**kwargs):
        assert 'fast-gen' not in url
        if method.upper()=='POST' and '/compatible/' in url:
            posts.append(kwargs);return image_response()
        raise AssertionError('Unexpected HTTP request: '+method+' '+url)
    monkeypatch.setattr(requests.sessions.Session,'request',http)
    pipeline.make_video(audio,provider='fastgen',mode='images',out_dir=str(tmp_path/'out'))
    assert not posts and old_frames[0].read_bytes()==original
    old_frames[0].unlink()
    final=pipeline.make_video(audio,provider='fastgen',mode='images',out_dir=str(tmp_path/'out'))
    assert len(posts)==1 and Path(final).exists() and wf.calls['stt']==1
    blocked.assert_not_called()


def test_sonnet_uses_real_selected_model_in_all_chat_requests(tmp_path,monkeypatch):
    import openai_api
    cfg=config();client=studio.text_client(routing.effective(cfg))
    sent=[]
    def request(*args,**kwargs):
        sent.append(kwargs['json'])
        return response({'choices':[{'message':{'content':'{"script":"Ready narration."}'},'finish_reason':'stop'}]})
    # Disable streaming only in this transport fixture; keep the production model selection unchanged.
    client.api.stream=False;monkeypatch.setattr(client.api.s,'post',request)
    assert client.chat_json('Write a script','Facts')['script']=='Ready narration.'
    assert sent[0]['model']=='anthropic/claude-sonnet-5.5-provider-4'


def test_elysium_only_account_keeps_virtual_fastgen_preferences_after_engine_selection(tmp_path):
    cfg=config();cfg.pop('fastgen_api_key');app.save_cfg(cfg)
    app.apply_engine(['fastgen'])
    assert app.load_cfg()['llm_engine']==app.load_cfg()['stt_engine']=='fastgen'
    assert app.Api()._validate_common(['fastgen'],{'mode':'images'}) is None


def test_readonly_checks_do_not_release_cancellation_during_handover():
    app.STATE['service_switch']={'state':'stopping'};pipeline.CANCEL.set()
    app.Api()._reset_idle_cancel()
    assert pipeline.CANCEL.is_set()
