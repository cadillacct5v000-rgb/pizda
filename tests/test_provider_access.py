import json
from unittest.mock import Mock

import pytest

import endpoints
import provider_access as access
import pipeline
import source_analysis
import rewrite
from openai_api import OpenAIClient, OpenAIError


def response(body, status=200):
    value = Mock(status_code=status, text=json.dumps(body), headers={})
    value.json.return_value = body
    value.__enter__ = Mock(return_value=value)
    value.__exit__ = Mock(return_value=False)
    return value


@pytest.fixture(autouse=True)
def urls():
    before = dict(endpoints.CUSTOM)
    endpoints.configure({})
    yield
    endpoints.CUSTOM.clear()
    endpoints.CUSTOM.update(before)


def test_google_invalid_gateway_key_has_actionable_redacted_message():
    key = 'sk-only-a-test-value'
    result = access.failure('gemini', response({'error': {'message': 'API key not valid. ' + key}}, 400), key, 'test-model')
    assert key not in result
    assert 'generativelanguage.googleapis.com/v1beta' in result
    assert 'не ошибка баланса' in result
    assert 'посредника' in result
    assert 'AI' in result


def test_location_never_returns_url_credentials_or_query():
    assert access.location('openai', 'https://user:secret@relay.example/v1?key=private') == 'relay.example/v1'


def test_gemini_model_discovery_filters_and_paginates(monkeypatch):
    get = Mock(side_effect=[response({'models': [{'name':'models/embedding', 'supportedGenerationMethods':['embedContent']}, {'name':'models/vision-fixture', 'supportedGenerationMethods':['generateContent']}], 'nextPageToken':'next'}), response({'models':[{'name':'models/text-fixture','supportedGenerationMethods':['generateContent']}]})])
    monkeypatch.setattr(access.requests, 'get', get)
    assert access.models('gemini', 'fixture-key') == ['text-fixture', 'vision-fixture']
    assert get.call_args.kwargs['params']['pageToken'] == 'next'
    assert get.call_args.kwargs['headers']['x-goog-api-key'] == 'fixture-key'


def test_elysium_discovery_uses_gateway_bearer_only(monkeypatch):
    get = Mock(return_value=response({'data':[{'id':'claude-fixture'}, {'id':'gemini-fixture'}]}))
    monkeypatch.setattr(access.requests, 'get', get)
    assert access.models('elysium', 'fixture-key') == ['claude-fixture','gemini-fixture']
    assert get.call_args.args[0] == 'https://elysiumai.garden/v1/models'
    assert get.call_args.kwargs['headers'] == {'Authorization':'Bearer fixture-key'}


def test_elysium_text_and_vision_keep_native_openai_separate(tmp_path):
    from PIL import Image
    image = tmp_path / 'frame.png'
    Image.new('RGB',(20,20)).save(image)
    client = OpenAIClient('fixture', provider='elysium')
    client.s.post = Mock(return_value=response({'choices':[{'message':{'content':'```json\n{"ok":true}\n```'}}]}))
    assert client.chat_json('Return JSON.', 'hello', model='gemini-fixture') == {'ok':True}
    assert client.see_json('Return JSON.', 'look', str(image), model='claude-fixture') == {'ok':True}
    for call in client.s.post.call_args_list:
        assert call.args[0] == 'https://elysiumai.garden/v1/chat/completions'
        assert 'max_tokens' in call.kwargs['json']
        assert 'response_format' not in call.kwargs['json']
        assert 'max_completion_tokens' not in call.kwargs['json']
    assert client.s.headers['Authorization'] == 'Bearer fixture'
    assert endpoints.base('openai') == 'https://api.openai.com/v1'


def test_pipeline_elysium_text_qa_and_source_route(monkeypatch):
    cfg = {'elysium_api_key':'fixture', 'elysium_model':'gemini-fixture', 'llm_engine':'elysium','stt_engine':'fastgen','fastgen_api_key':'fast-fixture','qa_images':True,'qa_engine':'elysium','qa_model':'claude-fixture','source_analysis_engine':'elysium'}
    _, _, engine, client = pipeline.make_text_engine(cfg)
    assert engine == 'elysium' and client.provider == 'elysium'
    assert pipeline.LLM(engine,client,'gemini-fixture').model == 'gemini-fixture'
    qa = pipeline.make_qa_client(cfg)
    assert qa.c.provider == 'elysium' and qa.model == 'claude-fixture'
    assert source_analysis._provider(cfg) == ('elysium','fixture','gemini-fixture')
    request = Mock(return_value={'choices':[{'message':{'content':'{"ok":true}'}}]})
    monkeypatch.setattr(source_analysis,'_http',request)
    assert source_analysis._query('elysium','fixture','gemini-fixture','hello') == {'ok':True}
    assert request.call_args.args[1] == 'https://elysiumai.garden/v1/chat/completions'
    assert request.call_args.kwargs['headers'] == {'Authorization':'Bearer fixture'}


def test_elysium_rewrite_uses_configured_model(monkeypatch):
    post = Mock(return_value=response({'choices':[{'message':{'content':'New script.'}}]}))
    monkeypatch.setattr(rewrite.requests,'post',post)
    assert rewrite.chat_text('elysium','fixture','gemini-fixture','rewrite',100) == 'New script.'
    assert post.call_args.args[0] == 'https://elysiumai.garden/v1/chat/completions'
    assert post.call_args.kwargs['json']['model'] == 'gemini-fixture'


def test_billing_429_stops_without_repeating(monkeypatch):
    failed = response({'error':{'code':'insufficient_quota','message':'Insufficient credit'}},429)
    client = OpenAIClient('fixture',provider='elysium')
    client.s.post = Mock(return_value=failed)
    pause = Mock()
    monkeypatch.setattr('netutil.pause',pause)
    with pytest.raises(OpenAIError,match='Elysium.*429'):
        client.chat_json('JSON','hello')
    assert client.s.post.call_count == 1
    request = Mock(return_value=failed)
    monkeypatch.setattr(source_analysis.requests,'request',request)
    with pytest.raises(source_analysis.SourceAnalysisError,match='Elysium.*429'):
        source_analysis._http('POST','https://elysiumai.garden/v1/chat/completions',headers={'Authorization':'Bearer fixture'})
    assert request.call_count == 1
    pause.assert_not_called()


def test_model_check_does_not_require_permission_to_list_models(monkeypatch, tmp_path):
    import app
    from llm_api import GeminiClient
    monkeypatch.setattr(app,'load_cfg',lambda: {'gemini_api_key':'fixture','gemini_model':'vision-fixture'})
    monkeypatch.setattr(app,'data_dir',lambda: str(tmp_path))
    listed = Mock(side_effect=AssertionError('Models listing must be independent of generation test'))
    monkeypatch.setattr(GeminiClient,'check',listed)
    monkeypatch.setattr(GeminiClient,'chat_json',lambda *a,**k: {'ok':True})
    assert app.Api().check_keys(['gemini'])['gemini'] == 'OK · vision-fixture отвечает'
    listed.assert_not_called()
