"""Task-specific model menus, read-only connections, and isolated short speech previews."""
import base64
import copy
import time
import subprocess
from pathlib import Path
from unittest.mock import Mock
import pytest
import app
import pipeline
import provider_access as access
import source_analysis


@pytest.fixture
def local(tmp_path,monkeypatch):
    monkeypatch.setattr(app,'CONFIG_PATH',str(tmp_path/'config.json'))
    monkeypatch.setattr(app,'data_dir',lambda:str(tmp_path))
    monkeypatch.setattr(app,'STATE',{**copy.deepcopy(app.STATE),'running':False,'preparing':False,'queue_active':False,'service_switch':None,'log':[]})
    monkeypatch.setattr(app,'_VOICE_SAMPLE',None)
    monkeypatch.setattr(app,'_VOICE_SAMPLE_ACTIVE',False)
    pipeline.CANCEL.clear()
    yield tmp_path
    pipeline.CANCEL.clear()


@pytest.mark.parametrize('model',['seedream/4.5','bytedance/seedream','kling-2.6','kling-3.0/video','x-ai/grok-imagine-video','google/gemini-3.1-flash-image-preview','elevenlabs/text-to-speech-v3-provider-4','openai/gpt-image-1.5','gpt-4o-image','qwen-image','gpt-4o-transcribe','gpt-audio','gpt-realtime'])
def test_media_models_never_enter_chat_or_vision_pickers(model):
    assert model not in access.filter_models([model],'text')
    assert model not in access.filter_models([model],'vision')
    with pytest.raises(ValueError,match='не подходит'):pipeline.make_qa_client({'qa_images':True,'qa_engine':'elysium','elysium_api_key':'fixture','qa_model':model})
    with pytest.raises(ValueError,match='не подходит'):source_analysis._provider({'source_analysis_engine':'elysium','elysium_api_key':'fixture','source_analysis_model':model})


def test_readonly_catalogs_separate_task_choices(local,monkeypatch):
    values=['seedream/4.5','kling-2.6','google/gemini-3.6-flash','anthropic/claude-sonnet-5.5','deepseek/deepseek-chat']
    app.save_cfg({'elysium_api_key':'fixture'})
    read=Mock(return_value=values);monkeypatch.setattr(access,'models',read)
    vision=app.Api().available_models('elysium','vision')
    assert vision['models']==['google/gemini-3.6-flash','anthropic/claude-sonnet-5.5']
    assert app.Api().available_models('elysium','text')['models']==values[2:]
    assert vision['omitted']==3
    assert app.Api().check_connection('elysium')['ok']
    assert read.call_count==3


def test_fastgen_connection_check_never_starts_paid_probe(local,monkeypatch):
    app.save_cfg({'fastgen_api_key':'fixture'})
    fake=Mock();monkeypatch.setattr(app,'FastGen',Mock(return_value=fake))
    assert app.Api().check_connection('fastgen')['ok']
    fake.usage.assert_called_once();fake.s.post.assert_not_called()


@pytest.mark.parametrize('cfg',[{}, {'fastgen_api_key':'   '}, {'image_keys':{'fastgen':['',' ']}}])
def test_empty_fastgen_connection_has_actionable_error_without_network(local,monkeypatch,cfg):
    app.save_cfg(cfg)
    constructor=Mock();monkeypatch.setattr(app,'FastGen',constructor)
    result=app.Api().check_connection('fastgen')
    assert not result['ok'] and 'Введите ключ FastGen' in result['error']
    assert 'index' not in result['error'];constructor.assert_not_called()


@pytest.mark.parametrize('index',[-1,3,None,True,'missing'])
def test_missing_fastgen_row_never_checks_another_key(local,monkeypatch,index):
    app.save_cfg({'fastgen_api_key':'fixture'})
    constructor=Mock();monkeypatch.setattr(app,'FastGen',constructor)
    result=app.Api().check_connection('fastgen',index)
    assert not result['ok'] and 'index' not in result['error']
    constructor.assert_not_called()


@pytest.mark.parametrize('language',['ro','pt','es'])
def test_voice_plan_does_not_translate_or_start_synthesis_for_russian_draft(local,monkeypatch,language):
    worker=Mock();monkeypatch.setattr(app.threading,'Thread',worker)
    result=app.Api().speech_sample({'text':'Этот сценарий пока написан на русском языке. '*4,
        'language':language,'prepare_only':True,'reference_mode':'saved'})
    assert not result['ok'] and 'на выбранном языке' in result['error']
    worker.assert_not_called()


def test_voice_preview_is_short_cached_and_independent_of_text_models(local,monkeypatch):
    app.save_cfg({'tts_service':'elevenlabs','elevenlabs_api_key':'fixture','elysium_replace_fastgen':True})
    calls=[]
    tone=local/'tone.mp3'
    subprocess.run([pipeline.ffmpeg_bin(),'-y','-v','error','-f','lavfi','-i','sine=frequency=440:duration=1','-c:a','libmp3lame','-b:a','128k',str(tone)],check=True)
    audio=tone.read_bytes()
    class Speaker:
        def synthesize(self,text,voice_id,out,progress=None,language=None):
            calls.append((text,voice_id,language));Path(out).write_bytes(audio)
    monkeypatch.setattr(pipeline,'make_tts',lambda *a:Speaker())
    monkeypatch.setattr(pipeline,'make_context',Mock(side_effect=AssertionError('No images for speech preview')))
    monkeypatch.setattr(pipeline,'make_text_engine',Mock(side_effect=AssertionError('No ASR or text for speech preview')))
    def preview(opts):
        result=app.Api().voice_sample(opts)
        deadline=time.monotonic()+10
        while app.STATE['preparing'] and time.monotonic()<deadline:time.sleep(.01)
        assert not app.STATE['preparing']
        return result
    opts={'text':'Русская речь должна быть понятной. Проверьте ударения и интонацию.','tts':'elevenlabs','voice':'russian-fixture'}
    assert preview(opts)['ok']
    assert not app.STATE['preparing'] and app.STATE['stage']=='voice_sample_done'
    result=app.Api().voice_sample_result();assert result['ok']
    assert base64.b64decode(result['audio'].split(',')[1])==audio
    assert calls==[(opts['text'],'russian-fixture','ru')]
    app.save_cfg({**app.load_cfg(),'elysium_model':'anthropic/claude-sonnet-5.5'})
    assert preview(opts)['ok'] and len(calls)==1
    assert preview({**opts,'voice':'different'})['ok'] and len(calls)==2
    assert not list(local.glob('output/*.mp4'))


def test_voice_preview_rejects_full_script_or_busy_job_before_any_generation(local):
    app.save_cfg({'tts_service':'edge'})
    assert not app.Api().voice_sample({'text':'x'*601,'tts':'edge'})['ok']
    app.STATE['running']=True
    assert not app.Api().voice_sample({'text':'Проба.','tts':'edge'})['ok']
    assert app.STATE['running']


def test_wrong_media_model_is_blocked_before_paid_chat_or_file_upload():
    from openai_api import OpenAIClient
    client=OpenAIClient('fixture',provider='elysium');client.s.post=Mock()
    with pytest.raises(ValueError,match='не подходит'):client.chat_json('JSON','Check',model='kling-2.6')
    with pytest.raises(ValueError,match='не подходит'):client.see_json('JSON','Check','does-not-exist.png',model='seedream/4.5')
    client.s.post.assert_not_called()


def test_preflight_rejects_wrong_qa_model_before_voice_or_images(local):
    app.save_cfg({'elysium_api_key':'fixture','qa_images':True,'qa_engine':'elysium','qa_model':'kling-2.6'})
    assert 'не подходит' in app.Api()._validate_common([],{'mode':'images'})


def test_catalog_metadata_separates_ambiguous_models_by_modality():
    ids=access.ModelIds(['custom-vision','custom-text','custom-movie'],{
        'custom-vision':{'input_modalities':['text','image'],'output_modalities':['text']},
        'custom-text':{'input_modalities':['text'],'output_modalities':['text']},
        'custom-movie':{'kind':'video'}})
    assert access.filter_models(ids,'vision')==['custom-vision']
    assert access.filter_models(ids,'text')==['custom-vision','custom-text']


def test_voice_stop_does_not_cancel_an_unrelated_render(local):
    app.STATE['running']=True
    assert not app.Api().cancel_voice_sample()['ok']
    assert not pipeline.CANCEL.is_set() and app.STATE['running']


@pytest.mark.parametrize('bad_field',['elysium_model','source_analysis_model'])
def test_rewrite_rejects_media_models_before_starting_source_work(local,monkeypatch,bad_field):
    app.save_cfg({'elysium_api_key':'fixture','rewrite_engine':'elysium','source_analysis_engine':'elysium',bad_field:'kling-2.6'})
    worker=Mock();monkeypatch.setattr(app,'run_rewrite',worker)
    result=app.Api().rewrite({'rewrite':{'on':True,'source':'youtube','url':'https://www.youtube.com/watch?v=fixture','engine':'elysium','analyze_video':True}})
    assert not result['ok'] and 'не подходит' in result['error']
    worker.assert_not_called()


def test_rewrite_language_uses_saved_selection_or_russian_for_russian_ui(local):
    app.save_cfg({'lang':'ru'})
    assert app.Api()._rewrite_opts({'rewrite':{'on':True}})['language']=='русском'
    app.save_cfg({**app.load_cfg(),'rewrite_lang':'английском'})
    assert app.Api()._rewrite_opts({'rewrite':{'on':True}})['language']=='английском'


@pytest.mark.parametrize('method',['start','queue_start','youtube_sample'])
def test_empty_image_selection_never_silently_falls_back_to_fastgen(local,method):
    result=getattr(app.Api(),method)({'providers':[]})
    assert not result['ok'] and 'сервис картинок' in result['error']
    assert not app.STATE['running'] and not app.STATE['preparing']


def test_own_script_storyboard_does_not_require_youtube_or_image_provider(local,monkeypatch):
    app.save_cfg({'llm_engine':'elysium','elysium_api_key':'fixture','style':'custom'})
    app.STATE.update(source_profile_id=None,source_profile=None)
    class Pending:
        def __init__(self,target,args=(),**kwargs):self.args=args
        def start(self):pass
    thread=Mock(side_effect=Pending);monkeypatch.setattr(app.threading,'Thread',thread)
    result=app.Api().prepare_storyboard({'script':'Собственный сценарий о морских животных.','source':'script','style':'custom','custom_style':'Flat blue illustration','providers':[]})
    assert result['ok']
    args=thread.call_args.kwargs['args'];assert args[1]['content_only']
    assert args[1]['visual_style']['description']=='Flat blue illustration' and args[1]['montage']['shots']==[]


def test_stale_source_analysis_stays_rejected_and_empty_custom_style_does_not_silently_change(local):
    app.save_cfg({'elysium_api_key':'fixture','llm_engine':'elysium','style':'custom'})
    app.STATE.update(source_profile_id='new-profile',source_profile={})
    result=app.Api().prepare_storyboard({'script':'Сценарий.','source_profile_id':'old-profile'})
    assert not result['ok'] and 'устарел' in result['error']
    result=app.Api().prepare_storyboard({'script':'Сценарий.','style':'custom','custom_style':''})
    assert not result['ok'] and 'пункте 2' in result['error']


def test_content_only_delivery_changes_audio_identity_but_visual_style_does_not(local,monkeypatch):
    from tests.test_source_narration import DELIVERY
    monkeypatch.setattr(pipeline,'data_dir',lambda:str(local))
    monkeypatch.setattr(pipeline,'make_voiceover',lambda cfg,text,service,voice,folder,progress:str(Path(folder)/'voiceover.mp3'))
    a={'content_only':True,'montage':{'shots':[]},'visual_style':{'description':'Blue illustration'},'narration':{'delivery':copy.deepcopy(DELIVERY)}}
    b=copy.deepcopy(a);b['visual_style']['description']='Red illustration'
    c=copy.deepcopy(a);c['narration']['delivery']['tempo_wpm']+=10
    cfg={'tts_service':'elevenlabs','tts_voice':'fixture'}
    directories=[pipeline.resolve_source(cfg,None,'Same narration.',None,None,lambda *args:None,source_profile=p)[1] for p in (a,b,c)]
    assert directories[0]==directories[1] and directories[0]!=directories[2]

@pytest.mark.parametrize('language',['ro','pt','es'])
def test_automatic_lumean_chooses_native_catalog_only_when_preset_missing(local,monkeypatch,language):
    import tts_api
    client=Mock();client.voices.return_value=[{'id':'native-'+language,'lang':language}]
    factory=Mock(return_value=client);monkeypatch.setattr(tts_api,'make_tts',factory)
    assert app.automatic_native_voice({'lumean_api_key':'fixture'},'lumean','',language)=='native-'+language
    client.voices.assert_called_once_with(language=language)
    client.voices.reset_mock()
    assert app.automatic_native_voice({},'lumean','my-saved-channel-voice',language)=='my-saved-channel-voice'
    client.voices.assert_not_called()


def test_automatic_voice_follows_language_and_keeps_cached_native_choice(local,monkeypatch):
    import tts_api
    client=Mock();client.voices.side_effect=lambda language:[{'id':'native-'+language,'lang':language}]
    monkeypatch.setattr(tts_api,'make_tts',lambda *a:client)
    cfg={'lumean_api_key':'fixture'}
    first=app.automatic_native_voice(cfg,'lumean','','ro')
    second=app.automatic_native_voice(cfg,'lumean',first,'es')
    assert (first,second)==('native-ro','native-es')
    assert app.automatic_native_voice(cfg,'lumean',second,'es')==second
    assert client.voices.call_count==2


def test_automatic_start_derives_custom_design_without_manual_checkpoint(local,monkeypatch):
    app.save_cfg({'fastgen_api_key':'fixture','tts_service':'edge'})
    monkeypatch.setattr(app,'selected_channel_design',lambda *a,**k:None)
    monkeypatch.setattr(app.Api,'_validate',lambda *a:None)
    monkeypatch.setattr(app,'production_check',lambda *a:{'blockers':[]})
    worker=Mock();monkeypatch.setattr(app.threading,'Thread',worker)
    options={'automatic':True,'source':'script','providers':['fastgen'],'tts':'edge','style':'custom','custom_style':'',
             'studio':{'enhancement_level':'original'},'rewrite':{'on':True,'source':'text','text':'A whale swims.','language':'румынском','engine':'fastgen'}}
    result=app.Api().start(options)
    assert result['ok'],result
    assert worker.call_args.kwargs['kwargs']['config_snapshot']['_automatic_speech'] is True
    assert worker.call_args.kwargs['args'][8]['language']=='румынском'
