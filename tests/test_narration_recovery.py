"""Received audio analysis can be recovered without paying to upload audio again."""
import copy
import json
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest
import requests

import app
import endpoints
import pipeline
import rewrite
import source_analysis
import source_narration as voice
import youtube_source
from elevenlabs_api import ElevenLabs
from test_source_narration import DELIVERY


def reply(text, reason='STOP', thought=False):
    value=requests.Response();value.status_code=200;value._content_consumed=True
    value._content=json.dumps({'candidates':[{'finishReason':reason,'content':{'parts':[{'text':text,'thought':thought}]}}]}).encode()
    return value


def malformed():
    # Same failure as the screenshot: no opening JSON object at all.
    return '\n'.join(f'{name}: {json.dumps(value)}' for name,value in DELIVERY.items())


@pytest.fixture(autouse=True)
def isolate(tmp_path,monkeypatch):
    pipeline.CANCEL.clear()
    monkeypatch.setattr(pipeline,'data_dir',lambda:str(tmp_path))
    monkeypatch.setattr(app,'CONFIG_PATH',str(tmp_path/'config.json'))
    monkeypatch.setattr(pipeline,'LOG_PATH',str(tmp_path/'videogen.log'))
    monkeypatch.setattr(endpoints,'CUSTOM',{})
    yield
    pipeline.CANCEL.clear()


@pytest.fixture
def media(tmp_path):
    audio=tmp_path/'source.wav';silent=tmp_path/'reference.mp4';speech=tmp_path/'speech.mp3'
    subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','sine=frequency=180:duration=2','-ar','16000','-ac','1',str(audio)],check=True)
    subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','color=c=black:s=64x64:r=10:d=2','-an',str(silent)],check=True)
    subprocess.run(['ffmpeg','-y','-v','error','-i',str(audio),'-c:a','libmp3lame',str(speech)],check=True)
    return audio,silent,speech


def test_schema_and_single_text_only_repair_then_complete_cache_reuse(tmp_path,monkeypatch,media):
    post=Mock(side_effect=[reply(malformed()),reply(json.dumps(DELIVERY))]);monkeypatch.setattr(voice.requests,'post',post)
    for _ in range(2):
        result=voice.analyse(media[0],2.,{'elysium_api_key':'synthetic-key'},tmp_path)
        assert result['delivery']==DELIVERY
    assert post.call_count==2
    first,repair=[c.kwargs['json'] for c in post.call_args_list]
    assert first['generationConfig']['responseSchema']==voice.SCHEMA
    assert first['generationConfig']['maxOutputTokens']>=6000
    assert any('inlineData' in p for p in first['contents'][0]['parts'])
    assert not any('inlineData' in p for p in repair['contents'][0]['parts'])
    assert 'You are NOT listening to audio' in repair['systemInstruction']['parts'][0]['text']
    assert all('synthetic-key' not in p.read_text() for p in tmp_path.glob('source_narration_*.json'))


def test_response_received_before_crash_is_reused_without_audio_reupload(tmp_path,monkeypatch,media):
    post=Mock(return_value=reply(malformed()));monkeypatch.setattr(voice.requests,'post',post)
    restore=voice._restore
    monkeypatch.setattr(voice,'_restore',Mock(side_effect=RuntimeError('simulated process interruption')))
    with pytest.raises(RuntimeError,match='interruption'):voice.analyse(media[0],2.,{'elysium_api_key':'fixture'},tmp_path)
    assert post.call_count==1
    monkeypatch.setattr(voice,'_restore',restore)
    post.reset_mock();post.return_value=reply(json.dumps(DELIVERY))
    assert voice.analyse(media[0],2.,{'elysium_api_key':'fixture'},tmp_path)['delivery']==DELIVERY
    assert post.call_count==1
    assert not any('inlineData' in p for p in post.call_args.kwargs['json']['contents'][0]['parts'])


@pytest.mark.parametrize('text,reason,thought',[('', 'STOP',False),('private reasoning','STOP',True),('{"speech_present":true','MAX_TOKENS',False)])
def test_empty_thought_only_or_truncated_reply_cannot_become_fake_observations(tmp_path,monkeypatch,media,text,reason,thought):
    post=Mock(return_value=reply(text,reason,thought));monkeypatch.setattr(voice.requests,'post',post)
    with pytest.raises(voice.NarrationError,match='не завершён'):
        voice.analyse(media[0],2.,{'elysium_api_key':'fixture'},tmp_path)
    assert post.call_count==1  # no same-attempt automatic audio or repair request
    # A later explicit retry is allowed to obtain real observations.
    post.return_value=reply(json.dumps(DELIVERY))
    assert voice.analyse(media[0],2.,{'elysium_api_key':'fixture'},tmp_path)['delivery']==DELIVERY
    assert post.call_count==2


def test_repair_must_not_change_measured_values(tmp_path,monkeypatch,media):
    wrong={**DELIVERY,'tempo_wpm':100}
    post=Mock(side_effect=[reply(malformed()),reply(json.dumps(wrong))]);monkeypatch.setattr(voice.requests,'post',post)
    with pytest.raises(voice.NarrationError,match='изменило измерение tempo_wpm'):
        voice.analyse(media[0],2.,{'elysium_api_key':'fixture'},tmp_path)
    assert post.call_count==2


def test_repair_failure_keeps_received_audio_observations_for_text_only_retry(tmp_path,monkeypatch,media):
    post=Mock(side_effect=[reply(malformed()),requests.ReadTimeout('repair timeout')]);monkeypatch.setattr(voice.requests,'post',post)
    with pytest.raises(requests.ReadTimeout):voice.analyse(media[0],2.,{'elysium_api_key':'fixture'},tmp_path)
    post.reset_mock();post.side_effect=None;post.return_value=reply(json.dumps(DELIVERY))
    assert voice.analyse(media[0],2.,{'elysium_api_key':'fixture'},tmp_path)['delivery']==DELIVERY
    assert post.call_count==1
    assert not any('inlineData' in p for p in post.call_args.kwargs['json']['contents'][0]['parts'])


def test_complete_source_preparation_and_eleven_synthesis_after_malformed_analysis(tmp_path,monkeypatch,media):
    audio,silent,speech=media
    monkeypatch.setattr(rewrite,'youtube_transcript',lambda *a,**k:'The original narrator describes an event.')
    monkeypatch.setattr(youtube_source,'video',lambda *a,**k:str(silent))
    download=Mock(return_value=str(audio));monkeypatch.setattr(youtube_source,'audio',download)
    profile={'sampling':{'duration':2.,'limitations':[]},'visual_style':{'description':'ink'},'narrative_style':'chronological'}
    monkeypatch.setattr(source_analysis,'analyse',lambda *a,**k:copy.deepcopy(profile))
    post=Mock(side_effect=[reply(malformed()),reply(json.dumps(DELIVERY))]);monkeypatch.setattr(voice.requests,'post',post)
    rewrite_text=Mock(return_value='The rewritten narrator describes an event.');monkeypatch.setattr(rewrite,'rewrite',rewrite_text)
    cfg={'tts_service':'elevenlabs','tts_voice':'voice','elevenlabs_api_key':'voice-key','elysium_api_key':'text-key','enhancement_level':'faithful'}
    rw={'source':'youtube','url':'https://youtu.be/dQw4w9WgXcQ','analyze_video':True,'engine':'elysium','language':'английском'}
    profiles=[]
    script=pipeline.rewrite_script(cfg,rw,lambda *a:None,profiles.append)
    assert script.startswith('The rewritten') and profiles[-1]['narration']['delivery']==DELIVERY
    rewrite_text.assert_called_once()
    assert download.call_count==1 and post.call_count==2
    client=ElevenLabs('voice-key')
    response=requests.Response();response.status_code=200;response._content=speech.read_bytes();response._content_consumed=True;response.headers['Content-Type']='audio/mpeg'
    client.s.post=Mock(return_value=response)
    monkeypatch.setattr(pipeline,'make_tts',lambda *a:client)
    result,directory,base=pipeline.resolve_source(cfg,None,script,None,None,lambda *a:None,source_profile=profiles[-1])
    assert 1.9<pipeline.audio_duration(result)<2.2
    assert client.s.post.call_args.kwargs['json']['voice_settings']==voice.eleven_settings(DELIVERY)
    assert client.s.post.call_args.kwargs['json']['text']==script


def test_traceback_written_to_disk_with_configured_key_removed(tmp_path):
    app.save_cfg({'elysium_api_key':'synthetic-long-key'})
    try:
        raise RuntimeError('test failure synthetic-long-key')
    except RuntimeError:
        text=app._record_error_traceback()
    stored=(tmp_path/'videogen.log').read_text()
    assert 'Traceback' in text and 'RuntimeError' in stored
    assert 'synthetic-long-key' not in stored


def test_invalid_text_repair_retries_text_not_source_audio(tmp_path,monkeypatch,media):
    wrong={**DELIVERY,'tempo_wpm':100}
    post=Mock(side_effect=[reply(malformed()),reply(json.dumps(wrong))]);monkeypatch.setattr(voice.requests,'post',post)
    with pytest.raises(voice.NarrationError):voice.analyse(media[0],2.,{'elysium_api_key':'fixture'},tmp_path)
    post.reset_mock();post.side_effect=None;post.return_value=reply(json.dumps(DELIVERY))
    assert voice.analyse(media[0],2.,{'elysium_api_key':'fixture'},tmp_path)['delivery']==DELIVERY
    assert post.call_count==1
    assert not any('inlineData' in p for p in post.call_args.kwargs['json']['contents'][0]['parts'])
