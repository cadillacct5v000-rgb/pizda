import json
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest
import requests
import app
import pipeline
from elevenlabs_api import ElevenLabs
from tts_api import TTSError, make_tts


def response(status=200, data=None, audio=None):
    r = requests.Response()
    r.status_code = status
    r._content = audio if audio is not None else json.dumps(data or {}).encode()
    r._content_consumed = True
    r.headers['Content-Type'] = 'audio/mpeg' if audio is not None else 'application/json'
    return r

@pytest.fixture
def mp3(tmp_path):
    p = tmp_path / 'tone.mp3'
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi', '-i',
                    'sine=frequency=440:duration=0.25', '-c:a', 'libmp3lame', str(p)], check=True)
    return p.read_bytes()


def test_factory_and_private_key_roundtrip():
    api = app.Api()
    assert api.save_config({'elevenlabs': 'private-test-key', 'tts_service': 'elevenlabs'})['ok']
    state = api.get_state()['config']
    assert state['tts_service'] == 'elevenlabs'
    assert state['has_elevenlabs']
    assert 'private-test-key' not in json.dumps(state)
    assert api.reveal_key('elevenlabs') == 'private-test-key'
    api.save_config({'elevenlabs': state['elevenlabs']})
    assert api.reveal_key('elevenlabs') == 'private-test-key'
    api.save_config({'elevenlabs': ''})
    assert not api.get_state()['config']['has_elevenlabs']
    assert isinstance(make_tts('elevenlabs', 'test'), ElevenLabs)


def test_voices_pagination():
    client = ElevenLabs('test-key')
    client.s.get = Mock(side_effect=[response(data={'voices': [{'voice_id': 'one', 'name': 'One'}], 'has_more': True, 'next_page_token': 'next'}),
                                       response(data={'voices': [{'voice_id': 'two', 'name': 'Two'}], 'has_more': False})])
    assert [v['id'] for v in client.voices()] == ['one', 'two']
    assert client.s.get.call_args.kwargs['params']['next_page_token'] == 'next'
    assert client.s.headers['xi-api-key'] == 'test-key'
    assert 'Authorization' not in client.s.headers

@pytest.mark.parametrize('status,code', [(401,'invalid_api_key'),(403,'missing_permissions'),(401,'quota_exceeded'),(404,'voice_not_found')])
def test_explicit_error_without_post_retry(status, code, tmp_path):
    client = ElevenLabs('test-key')
    client.s.post = Mock(return_value=response(status, {'detail': {'status':code,'message':'reason'}}))
    with pytest.raises(TTSError, match=code):
        client.synthesize('Test', 'voiceid', str(tmp_path/'out.mp3'))
    assert client.s.post.call_count == 1
    assert not (tmp_path/'out.mp3').exists()


def test_direct_audio_and_model_contract(mp3, tmp_path):
    client = ElevenLabs('test-key')
    client.s.post = Mock(return_value=response(audio=mp3))
    out = tmp_path/'voiceover.part'
    client.synthesize('Hello world.', 'voiceid', str(out))
    assert client._valid_audio(out)
    args, kwargs = client.s.post.call_args
    assert args[0] == 'https://api.elevenlabs.io/v1/text-to-speech/voiceid'
    assert kwargs['json'] == {'text':'Hello world.', 'model_id':'eleven_multilingual_v2'}
    assert 'language_code' not in kwargs['json']


def test_resume_paid_fragments(mp3, tmp_path, monkeypatch):
    client = ElevenLabs('test-key')
    monkeypatch.setattr(client, 'CHUNK', 12)
    client.s.post = Mock(side_effect=[response(audio=mp3), requests.ConnectionError('broken')])
    text = 'First part. Second part.'
    out = tmp_path/'out.mp3'
    with pytest.raises(requests.ConnectionError):
        client.synthesize(text, 'voiceid', str(out))
    assert client.s.post.call_count == 2
    client.s.post = Mock(return_value=response(audio=mp3))
    client.synthesize(text, 'voiceid', str(out))
    assert client.s.post.call_count == 1
    assert client._valid_audio(out)
    assert not list(tmp_path.glob('.elevenlabs-*'))


def test_bad_download_never_cached(tmp_path):
    client = ElevenLabs('test-key')
    client.s.post = Mock(return_value=response(audio=b'not an mp3'))
    with pytest.raises(TTSError, match='поврежд'):
        client.synthesize('Test.', 'voiceid', str(tmp_path/'out.mp3'))
    assert not (tmp_path/'out.mp3').exists()
    assert not list(tmp_path.glob('.elevenlabs-*/*.mp3'))


def test_cancel_before_post(tmp_path):
    client = ElevenLabs('test-key')
    client.s.post = Mock()
    pipeline.CANCEL.set()
    with pytest.raises(pipeline.Cancelled):
        client.synthesize('Test', 'voiceid', str(tmp_path/'out.mp3'))
    client.s.post.assert_not_called()


def test_no_automatic_split_after_provider_rejection(monkeypatch, tmp_path):
    client = Mock()
    client.synthesize.side_effect = TTSError('401: invalid key')
    monkeypatch.setattr(pipeline, 'make_tts', lambda *args: client)
    with pytest.raises(TTSError):
        pipeline.make_voiceover({'voicegen_api_key':'test'}, 'long text. '*800, 'voicegen', 'voice', str(tmp_path), lambda *a:None)
    assert client.synthesize.call_count == 1


def test_royal_explains_credential_difference():
    app.save_cfg({'tts_service':'royal'})
    message = app.Api()._validate_source({'source':'script','script':'Hello','tts':'royal'})
    assert 'ElevenLabs напрямую' in message
    assert 'RoyalTechno' in message


def test_check_works_after_previous_cancel(monkeypatch):
    app.save_cfg({'elevenlabs_api_key':'test-key'})
    monkeypatch.setattr(ElevenLabs, 'check', lambda self: (pipeline.check_cancel(), 'OK')[1])
    pipeline.CANCEL.set()
    assert app.Api().check_tts('elevenlabs')['ok']
