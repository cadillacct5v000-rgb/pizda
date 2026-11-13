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


@pytest.mark.parametrize('first', [0, 1])
def test_batch_local_numbering_matches_supplied_noncontiguous_order(first):
    chunk = [{'index': i} for i in [125, 127, 149]]
    mapped = p.scene_prompt_results(reply(range(first, first + 3)), chunk)
    assert list(mapped) == [125, 127, 149]
    assert mapped[149]['prompt'].endswith(str(first + 2))


def test_stable_ids_override_renumbering_and_preserve_valid_partial_siblings():
    chunk = scenes(3)[1:]
    response = {'scenes': [
        {'index': 0, 'scene_id': p.prompt_scene_id(chunk[1]), 'prompt': 'correct last scene'},
        {'index': 1, 'scene_id': 'not-requested', 'prompt': 'wrong scene'},
    ]}
    assert p.scene_prompt_results(response, chunk) == {2: response['scenes'][0]}


def test_duplicate_stable_id_cannot_overwrite_scene():
    chunk = scenes(2)
    item = {'scene_id': p.prompt_scene_id(chunk[0]), 'prompt': 'room'}
    with pytest.raises(ValueError, match='повторила ID'):
        p.scene_prompt_results({'scenes': [item, item]}, chunk)


def test_user_report_150_cached_plus_last_scene_renumbered_one(tmp_path):
    style = {'style_key': 'test', 'style': 'ink drawing'}
    original = scenes(151)
    for scene in original[:150]:
        scene.update(raw_prompt='saved frame', prompt='saved frame', schema=p.PROMPT_SCHEMA, style_key='test')
    calls = []
    def renumber(system, user, **kwargs):
        calls.append(indices(user))
        return reply([1])  # exact failure from the last single-scene batch
    result = p.build_prompts(Mock(chat_json=renumber), style, original, str(tmp_path), lambda *a: None, False)
    assert calls == [[150]]
    assert len(result) == 151 and all(s.get('prompt') for s in result)
    assert all(s['raw_prompt'] == 'saved frame' for s in result[:150])


def test_partial_unmappable_batch_repaired_individually_without_cross_scene_assignment(tmp_path):
    original = scenes(4)
    calls = []
    def bad_batch(system, user, **kwargs):
        ids = indices(user)
        calls.append(ids)
        if len(ids) > 1:
            return reply([999, 1000])
        return {'scenes': [{'index': 999, 'prompt': f'Unique frame for {ids[0]}'}]}
    result = p.build_prompts(Mock(chat_json=bad_batch), {'style_key': 'test'}, original, str(tmp_path), lambda *a: None, False)
    assert all(s['raw_prompt'] == f'Unique frame for {s["index"]}' for s in result)
    assert [ids[0] for ids in calls if len(ids) == 1] == [0, 1, 2, 3]


def test_user_report_125_cached_then_batch_and_single_local_numbering(tmp_path):
    style = {'style_key': 'test'}
    original = scenes(151)
    for scene in original[:125]:
        scene.update(raw_prompt='saved frame', prompt='saved frame', schema=p.PROMPT_SCHEMA, style_key='test')
    calls = []
    def local_reply(system, user, **kwargs):
        ids = indices(user)
        calls.append(ids)
        return {'scenes': [{'index': pos + 1, 'prompt': f'New frame {index}'} for pos, index in enumerate(ids)]}
    result = p.build_prompts(Mock(chat_json=local_reply), style, original, str(tmp_path), lambda *a:None, False)
    assert len(calls) == 2
    assert sorted(i for call in calls for i in call) == list(range(125,151))
    assert all(s['raw_prompt'] == f'New frame {s["index"]}' for s in result[125:])
    assert all(s['raw_prompt'] == 'saved frame' for s in result[:125])
