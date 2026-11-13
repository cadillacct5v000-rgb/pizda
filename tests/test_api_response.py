import json
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest
import requests

import api_response
import app
import endpoints
from elevenlabs_api import ElevenLabs
from openai_api import OpenAIClient, _chat_params
import model_reply
import pipeline
import source_narration
from test_source_narration import DELIVERY, answer


def http(body, content_type='application/json', status=200):
    response=requests.Response();response.status_code=status
    response.headers['Content-Type']=content_type
    response._content=body.encode();response._content_consumed=True
    response.close=Mock()
    return response


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    pipeline.CANCEL.clear()
    monkeypatch.setattr(app,'CONFIG_PATH',str(tmp_path/'config.json'))
    monkeypatch.setattr(pipeline,'LOG_PATH',str(tmp_path/'log.txt'))
    monkeypatch.setattr(endpoints,'CUSTOM',{})
    yield
    pipeline.CANCEL.clear()


@pytest.mark.parametrize('body,ctype,kind',[('', 'application/json','пустой ответ'),('<html>private-key blocked</html>','text/html','HTML'),('not json private-key','text/plain','не в формате'),('[]','application/json','не в формате')])
def test_eleven_bad_voice_response_is_not_key_rejection_and_does_not_leak_or_retry(body,ctype,kind):
    client=ElevenLabs('private-key');response=http(body,ctype)
    client.s.get=Mock(return_value=response)
    client.s.post=Mock(side_effect=AssertionError('No paid TTS'))
    with pytest.raises(api_response.APIResponseError,match=kind) as error:client.check()
    assert 'HTTP 200' in str(error.value) and 'private-key' not in str(error.value)
    assert 'Expecting value' not in str(error.value)
    assert client.s.get.call_count==1 and not client.s.post.called
    assert client.s.get.call_args.kwargs['allow_redirects'] is False
    response.close.assert_called_once()


def test_voice_endpoint_validates_schema_before_success():
    client=ElevenLabs('fixture');client.s.get=Mock(return_value=http('{}'))
    with pytest.raises(RuntimeError,match='список voices'):client.check()
    client.s.get=Mock(return_value=http('{"voices":[]}'))
    assert client.check().startswith('OK')


def test_eleven_redirect_does_not_forward_key():
    client=ElevenLabs('fixture');response=http('',status=302)
    response.headers['Location']='https://other.invalid/'
    client.s.get=Mock(return_value=response)
    with pytest.raises(RuntimeError,match='перенаправление'):client.check()
    assert client.s.get.call_count==1 and client.s.get.call_args.kwargs['allow_redirects'] is False


def test_elysium_key_check_uses_catalog_not_paid_200_token_probe():
    client=OpenAIClient('fixture',provider='elysium')
    client.s.get=Mock(return_value=http('{"data":[{"id":"openai/gpt-5.4"}]}'))
    client.s.post=Mock(side_effect=AssertionError('No paid generation in key check'))
    assert 'ключ принят' in client.check_model('openai/gpt-5.4')
    assert client.s.get.call_args.args[0]=='https://elysiumai.garden/v1/models'
    client.s.post.assert_not_called()
    with pytest.raises(RuntimeError,match='отсутствует'):client.check_model('missing-model')


def test_app_elysium_check_calls_new_read_only_path(monkeypatch):
    app.save_cfg({'elysium_api_key':'fixture','elysium_model':'openai/gpt-5.4'})
    check=Mock(return_value='OK · key accepted')
    monkeypatch.setattr(OpenAIClient,'check_model',check)
    monkeypatch.setattr(OpenAIClient,'chat_json',Mock(side_effect=AssertionError('Do not generate')))
    assert app.Api().check_keys(['elysium'])['elysium']=='OK · key accepted'
    check.assert_called_once_with('openai/gpt-5.4')


def test_elysium_nonstream_caller_accepts_mislabeled_sse_and_requests_json():
    events=['data: '+json.dumps({'choices':[{'index':0,'delta':{'content':'{"ok":true}'},'finish_reason':'stop'}]}),'','data: [DONE]','']
    response=http('\n'.join(events),'application/json')
    client=OpenAIClient('fixture',provider='elysium');client.s.post=Mock(return_value=response)
    assert client.chat_json('Return JSON.','hello',model='openai/gpt-5.4')=={'ok':True}
    assert client.s.post.call_count==1
    assert client.s.post.call_args.kwargs['json']['response_format']=={'type':'json_object'}
    response.close.assert_called_once()


def test_prefixed_reasoning_model_has_same_native_parameters():
    assert _chat_params('openai/gpt-5.4',200,.7)==_chat_params('gpt-5.4',200,.7)=={'max_completion_tokens':200}


def test_html_api_response_does_not_reissue_paid_generation(monkeypatch):
    response=http('<html>private-key blocked</html>','text/html')
    client=OpenAIClient('private-key',provider='elysium');client.s.post=Mock(return_value=response)
    with pytest.raises(model_reply.APIReplyError,match='HTTP 200, HTML') as error:
        client.chat_json('JSON','hello',model='openai/gpt-5.4')
    assert 'private-key' not in str(error.value) and client.s.post.call_count==1


def test_empty_model_content_is_distinguished_from_invalid_api_body():
    response=http('{"choices":[{"finish_reason":"stop","message":{"content":""}}]}')
    client=OpenAIClient('fixture',provider='elysium');client.s.post=Mock(return_value=response)
    with pytest.raises(model_reply.IncompleteReply,match='пустой текст'):
        client.chat_json('JSON','hello')
    assert client.s.post.call_count==1


def test_real_silent_reference_downloads_separate_audio_and_sends_actual_audio(tmp_path,monkeypatch):
    silent=tmp_path/'reference.mp4';audio=tmp_path/'narration.wav'
    subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','color=c=black:s=64x64:r=10:d=2','-an',str(silent)],check=True)
    subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','sine=frequency=180:duration=2','-ar','16000','-ac','1',str(audio)],check=True)
    import youtube_source
    download=Mock(return_value=str(audio));monkeypatch.setattr(youtube_source,'audio',download)
    post=Mock(return_value=answer());monkeypatch.setattr(source_narration.requests,'post',post)
    profile={'sampling':{'duration':2.,'limitations':[]},'visual_style':{'description':'ink'}}
    cfg={'tts_service':'elevenlabs','elysium_api_key':'fixture'}
    assert not source_narration.has_audio(silent) and source_narration.has_audio(audio)
    result=source_narration.attach_youtube(profile,str(silent),'https://youtu.be/dQw4w9WgXcQ',cfg,tmp_path)
    assert result['narration']['delivery']==DELIVERY
    assert download.call_count==1 and post.call_count==1
    # Existing audio (as in bounded previews) must never fetch the full source.
    download.reset_mock()
    source_narration.attach_youtube(profile,str(audio),'https://youtu.be/dQw4w9WgXcQ',cfg,tmp_path)
    download.assert_not_called()
    assert post.call_count==1  # identical successful audio analysis reused


def test_incomplete_mislabeled_stream_is_not_replayed_as_another_paid_request(monkeypatch):
    response=http('data: '+json.dumps({'choices':[{'index':0,'delta':{'content':'{"partial":true}'},'finish_reason':None}]})+'\n\n')
    client=OpenAIClient('fixture',provider='elysium');client.s.post=Mock(return_value=response)
    monkeypatch.setattr('netutil.pause',Mock(side_effect=AssertionError('No automatic retry')))
    with pytest.raises(RuntimeError,match='автоматически не повторяется'):
        client.chat_json('JSON','hello')
    assert client.s.post.call_count==1
    response.close.assert_called_once()


def test_sse_with_bom_and_event_prefix_is_decoded():
    body='\ufeffevent: message\n'+'data: '+json.dumps({'choices':[{'index':0,'delta':{'content':'{"ok":true}'},'finish_reason':'stop'}]})+'\n\n'
    response=http(body,'application/json')
    client=OpenAIClient('fixture',provider='elysium');client.s.post=Mock(return_value=response)
    assert client.chat_json('JSON','hello')=={'ok':True}
    assert client.s.post.call_count==1
