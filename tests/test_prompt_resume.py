"""Prompt batches must preserve completed paid work and stop before placeholder images."""
import json
import re
from unittest.mock import Mock

import pytest
import pipeline as p


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    p.CANCEL.clear()
    monkeypatch.setattr(p, 'LOG_PATH', str(tmp_path / 'log.txt'))
    yield
    p.CANCEL.clear()


def scenes(n=4):
    return [{'index': i, 'text': f'Scene {i}', 'duration': 3., 'start': i * 3.} for i in range(n)]


def indices(user):
    return [int(i) for i in re.findall(r'^(\d+)\. \[', user, re.M)]


def reply(ids):
    return {'scenes': [{'index': i, 'prompt': f'A detailed room scene {i}'} for i in ids]}


@pytest.mark.parametrize('offset', [0, 1])
def test_exact_indices_and_complete_legacy_one_based_reply(offset):
    assert set(p.scene_prompt_results(reply([5 + offset, 9 + offset]), [{'index': 5}, {'index': 9}])) == {5, 9}


@pytest.mark.parametrize('response', [reply([0, 0]), reply([8]), {'scenes': 'bad'}, {'scenes': [{'index': True, 'prompt': 'room'}]}])
def test_invalid_indices_rejected(response):
    with pytest.raises(ValueError):
        p.scene_prompt_results(response, scenes(2))


def test_partial_chunks_survive_failure_and_only_missing_scene_retried(tmp_path):
    calls = []
    def incomplete(system, user, **kwargs):
        ids = indices(user)
        calls.append(ids)
        assert 'zero-based' in system
        # Three chunks finish, one chunk is incomplete across all attempts.
        return reply([i for i in ids if i != 2])
    llm = Mock(chat_json=incomplete)
    style = {'style_key': 'test', 'style': 'ink drawing'}
    progress = []
    original = scenes()
    with pytest.raises(RuntimeError, match=r'1 из 4.*Готовые промпты \(3\) сохранены'):
        p.build_prompts(llm, style, original, str(tmp_path), lambda *a: progress.append(a), False, batch=2)
    saved = json.loads((tmp_path / 'scenes.json').read_text())
    assert all(s.get('prompt') for s in saved if s['index'] != 2)
    assert not saved[2].get('prompt') and not saved[2].get('raw_prompt')
    assert max(x[1] for x in progress) == 3
    retry_calls = []
    def complete(system, user, **kwargs):
        ids = indices(user)
        retry_calls.append(ids)
        return reply(ids)
    p.build_prompts(Mock(chat_json=complete), style, saved, str(tmp_path), lambda *a: None, False, batch=2)
    assert retry_calls == [[2]]
    assert all(s.get('prompt') and not s.get('placeholder') for s in saved)


def test_rejected_key_stops_new_batches_and_reports_actual_cause(tmp_path):
    llm = Mock()
    llm.chat_json.side_effect = RuntimeError('HTTP 401 invalid x-api-key')
    with pytest.raises(RuntimeError, match='HTTP 401 invalid x-api-key'):
        p.build_prompts(llm, {'style_key': 'test'}, scenes(50), str(tmp_path), lambda *a: None, False, batch=2)
    # Up to three requests may already be in flight, rather than 75 repeated failures.
    assert llm.chat_json.call_count <= 3
    saved = json.loads((tmp_path / 'scenes.json').read_text())
    assert all(not s.get('prompt') for s in saved)


def test_elysium_fallback_preferred_over_invalid_direct_provider(monkeypatch):
    create = Mock(return_value=object())
    monkeypatch.setattr(p, 'OpenAIClient', create)
    direct = Mock(side_effect=AssertionError('Direct Anthropic must not be selected'))
    monkeypatch.setattr(p, 'AnthropicClient', direct)
    result = p.text_fallback({'elysium_api_key': 'synthetic', 'elysium_model': 'chosen-text-model', 'anthropic_api_key': 'other'})
    assert result.engine == 'elysium' and result.model == 'chosen-text-model'
    create.assert_called_once_with('synthetic', provider='elysium')
    direct.assert_not_called()
