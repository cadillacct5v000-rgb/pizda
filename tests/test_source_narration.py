import base64
import copy
import io
import json
from pathlib import Path
import subprocess
from unittest.mock import Mock
import wave

import pytest
import requests

import endpoints
import pipeline
import source_narration as voice
from elevenlabs_api import ElevenLabs
from tts_api import FastGenTTS

DELIVERY = {'speech_present': True, 'instructions': 'A restrained, conversational narrator. Short pauses between thoughts, varied phrase endings and deliberate emphasis on discoveries.',
            'tempo_wpm': 175, 'pause_ms': 350, 'intonation_variation': .65, 'energy': .45}


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    pipeline.CANCEL.clear()
    monkeypatch.setattr(endpoints, 'CUSTOM', {})
    monkeypatch.setattr(pipeline, 'LOG_PATH', str(tmp_path/'log.txt'))
    yield
    pipeline.CANCEL.clear()


def answer(delivery=DELIVERY, status=200):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps({'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps(delivery)}]}}]}).encode()
    response._content_consumed = True
    return response


@pytest.fixture
def audio(tmp_path):
    path = tmp_path/'source.wav'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=180:duration=2', '-ar', '16000', '-ac', '1', str(path)], check=True)
    return path


def test_native_audio_is_actually_attached_and_success_cached(audio, tmp_path, monkeypatch):
    post = Mock(return_value=answer())
    monkeypatch.setattr(voice.requests, 'post', post)
    cfg = {'elysium_api_key': 'synthetic-secret'}
    first = voice.analyse(audio, 2., cfg, tmp_path)
    again = voice.analyse(audio, 2., cfg, tmp_path)
    assert first['delivery'] == again['delivery'] == DELIVERY
    post.assert_called_once()
    url = post.call_args.args[0]
    assert url == 'https://elysiumai.garden/compatible/v1beta/models/google/gemini-3.6-flash:generateContent'
    kwargs = post.call_args.kwargs
    assert kwargs['headers']['Authorization'] == 'Bearer synthetic-secret'
    assert 'synthetic-secret' not in url
    parts = kwargs['json']['contents'][0]['parts']
    sounds = [part['inlineData'] for part in parts if 'inlineData' in part]
    assert len(sounds) == 1 and sounds[0]['mimeType'] == 'audio/wav'
    with wave.open(io.BytesIO(base64.b64decode(sounds[0]['data']))) as wavefile:
        assert wavefile.getframerate() == 16000 and wavefile.getnchannels() == 1
        assert 1.9 <= wavefile.getnframes()/16000 <= 2.1
    assert 'synthetic-secret' not in next(tmp_path.glob('source_narration_*.json')).read_text()


@pytest.mark.parametrize('duration', [1., 19.5, 20., 30., 59.2, 60., 61., 3600.])
def test_sampled_audio_bounded_to_source_and_at_most_sixty_seconds(duration):
    ranges = voice.sample_ranges(duration)
    assert 1 <= len(ranges) <= 3
    assert sum(length for _, length in ranges) <= min(60, duration)
    assert all(0 <= start < duration and 0 < length <= 20 and start+length <= duration for start,length in ranges)


def test_no_audio_key_never_fabricates_delivery_or_posts(audio, tmp_path, monkeypatch):
    post = Mock(side_effect=AssertionError('No paid request'))
    monkeypatch.setattr(voice.requests, 'post', post)
    messages=[]
    assert voice.analyse(audio, 2., {'fastgen_api_key':'fixture'}, tmp_path, messages.append) is None
    assert messages and 'без анализа' in messages[0]


@pytest.mark.parametrize('status', [400, 401, 403, 502])
def test_provider_rejection_has_no_auto_retry_or_fake_cached_analysis(audio, tmp_path, monkeypatch, status):
    post = Mock(return_value=answer(status=status))
    monkeypatch.setattr(voice.requests, 'post', post)
    with pytest.raises(ValueError, match=f'HTTP {status}'):
        voice.analyse(audio, 2., {'elysium_api_key':'fixture'}, tmp_path)
    assert post.call_count == 1
    assert not list(tmp_path.glob('source_narration_*.json'))


@pytest.mark.parametrize('key,value', [('speech_present',False),('tempo_wpm',float('nan')),('pause_ms',True),('energy',2),('instructions','invented')])
def test_bad_analysis_rejected(key, value):
    invalid={**DELIVERY,key:value}
    with pytest.raises(ValueError):voice.validate(invalid)


def test_attach_is_independent_of_visual_analysis_and_does_not_mutate_old_profile(audio, tmp_path, monkeypatch):
    source={'sampling':{'limitations':['Голос, музыка и звуковые эффекты не анализировались.']},'visual_style':{'description':'ink'}}
    old=copy.deepcopy(source)
    monkeypatch.setattr(voice,'analyse',lambda *a:{'delivery':DELIVERY})
    profile=voice.attach(source,audio,2.,{'tts_service':'fastgen'},tmp_path)
    assert source == old and profile['narration']['delivery'] == DELIVERY
    assert voice.delivery_of(profile) == DELIVERY
    assert voice.delivery_of(source) is None
    assert voice.attach(source,audio,2.,{'tts_service':'edge'},tmp_path) is source


def test_gemini_uses_observed_delivery_without_changing_narration_words():
    client=FastGenTTS('fixture')
    client.api.run=Mock(return_value={'status':'succeeded','results':['audio']})
    client._one('Original words.', 'voice')
    assert client.api.run.call_args.args[0]['prompt'] == 'Original words.'
    client.source_delivery=DELIVERY
    client._one('Translated narration.', 'voice')
    prompt=client.api.run.call_args.args[0]['prompt']
    assert DELIVERY['instructions'] in prompt
    assert '<narration>\nTranslated narration.\n</narration>' in prompt
    assert client.api.run.call_args.args[0]['voice'] == 'voice'


def test_eleven_context_settings_and_fragment_cache_depend_on_source_delivery(tmp_path, monkeypatch):
    client=ElevenLabs('fixture');client.source_delivery=DELIVERY;client.CHUNK=35
    monkeypatch.setattr(client,'_valid_audio',lambda p:Path(p).is_file())
    def audio_reply(*args,**kwargs):
        result=requests.Response();result.status_code=200;result.headers['Content-Type']='audio/mpeg'
        result._content=b'fixture audio';result._content_consumed=True;return result
    client.s.post=Mock(side_effect=audio_reply)
    monkeypatch.setattr('elevenlabs_api.run_media',lambda args,**kwargs:(Path(args[-1]).write_bytes(b'joined'),Mock(returncode=0))[1])
    client.synthesize('First narrated paragraph.\n\nSecond narrated paragraph.','voice',str(tmp_path/'out.mp3'))
    payloads=[call.kwargs['json'] for call in client.s.post.call_args_list]
    assert len(payloads)==2 and payloads[0]['voice_settings']==voice.eleven_settings(DELIVERY)
    assert payloads[0]['next_text']=='Second narrated paragraph.'
    assert payloads[1]['previous_text']=='First narrated paragraph.'
    assert 'instructions' not in payloads[0]['text']


def test_changed_delivery_has_separate_audio_job_but_legacy_keeps_existing_identity(tmp_path,monkeypatch):
    monkeypatch.setattr(pipeline,'data_dir',lambda:str(tmp_path))
    make=Mock(return_value='voiceover.mp3');monkeypatch.setattr(pipeline,'make_voiceover',make)
    original={'sampling':{'duration':10}}
    enhanced={**original,'narration':{'delivery':DELIVERY}}
    cfg={'tts_service':'fastgen','tts_voice':'voice'}
    _,old_dir,_=pipeline.resolve_source(cfg,None,'Narration.',None,None,lambda *a:None,source_profile=original)
    _,new_dir,_=pipeline.resolve_source(cfg,None,'Narration.',None,None,lambda *a:None,source_profile=enhanced)
    assert old_dir!=new_dir
    assert make.call_args.args[0]['_source_delivery']==DELIVERY
    assert Path(old_dir).is_dir() and Path(new_dir).is_dir()
