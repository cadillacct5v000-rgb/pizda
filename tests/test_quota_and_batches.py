import json
from pathlib import Path
from unittest.mock import Mock

from PIL import Image
import pytest

import llm_api
import montage
import netutil
import quota_info
import source_analysis as analysis


def quota(identifier='GenerateRequestsPerDayPerProject-FreeTier', value='20', delay='12s'):
    return {'error': {'code': 429, 'message': 'Quota exceeded; private-fixture-key https://example.org/?key=private-fixture-key', 'details': [
        {'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{'quotaId': identifier, 'quotaValue': value, 'quotaDimensions': {'model': 'fixture-model'}}]},
        {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': delay}]}}


class Response:
    def __init__(self, status, payload):
        self.status_code, self.payload, self.headers = status, payload, {}

    def json(self):
        return self.payload

    def close(self):
        pass


@pytest.fixture(autouse=True)
def no_cancel(monkeypatch):
    monkeypatch.setattr(netutil, 'CANCEL_CHECK', None)


def test_quota_distinguishes_day_minute_zero_and_masks_keys():
    daily = quota_info.describe(quota(), secrets=('private-fixture-key',))
    assert daily['kind'] == 'daily' and 'суточная' in daily['message']
    assert 'fixture-model' in daily['message'] and '=20' in daily['message']
    assert 'private-fixture-key' not in daily['message']
    minute = quota_info.describe(quota('GenerateTokensPerMinute-FreeTier', '1000', '17.2s'))
    assert minute['kind'] == 'minute' and minute['retry_seconds'] == 18
    zero = quota_info.describe(quota(value='0'))
    assert zero['kind'] == 'zero' and 'квоту 0' in zero['message']


def test_daily_quota_stops_source_and_text_without_repeated_requests(monkeypatch):
    response = Response(429, quota())
    request = Mock(return_value=response)
    post = Mock(return_value=response)
    pause = Mock()
    monkeypatch.setattr(analysis.requests, 'request', request)
    monkeypatch.setattr(llm_api.requests, 'post', post)
    monkeypatch.setattr(netutil, 'pause', pause)
    with pytest.raises(analysis.SourceAnalysisError, match='суточная'):
        analysis._http('POST', 'https://example.org/analysis')
    client = llm_api.GeminiClient('private-fixture-key', 'fixture-model')
    with pytest.raises(llm_api.LLMError, match='суточная') as caught:
        client.chat_json('system', 'text')
    assert request.call_count == post.call_count == 1
    assert 'private-fixture-key' not in str(caught.value)
    pause.assert_not_called()


def test_minute_rpc_delay_respected_in_source_and_text(monkeypatch):
    limit = quota('GenerateRequestsPerMinute-FreeTier', '10', '17.2s')
    pauses = []
    monkeypatch.setattr(netutil, 'pause', pauses.append)
    monkeypatch.setattr(analysis.requests, 'request', Mock(side_effect=[Response(429, limit), Response(200, {'ok': True})]))
    assert analysis._http('POST', 'https://example.org/analysis') == {'ok': True}
    monkeypatch.setattr(llm_api.requests, 'post', Mock(side_effect=[Response(429, limit), Response(200, {'candidates': [{'content': {'parts': [{'text': '{"ok":true}'}]}, 'finishReason': 'STOP'}]})]))
    assert llm_api.GeminiClient('fixture', 'fixture-model').chat_json('s', 'u') == {'ok': True}
    assert pauses == [18, 18]


def test_retry_after_header_can_extend_rpc_delay():
    response = Response(429, quota('RequestsPerMinute', '10', '12s'))
    response.headers['Retry-After'] = '30'
    result = quota_info.from_response(response)
    assert result['retry_seconds'] == 30
    assert 'через 30 с' in result['message']


def batch_fixture(monkeypatch, count=13):
    shots = [{'index': index, 'start': float(index), 'end': float(index + 1), 'duration': 1.} for index in range(count)]
    monkeypatch.setattr(montage, 'detect_shots', lambda *args: shots)
    sampled = []
    def extract(video, directory, times):
        Path(directory).mkdir(parents=True, exist_ok=True)
        sampled.extend(times)
        frames = []
        for index, _ in enumerate(times):
            path = Path(directory) / f'frame_{index:04d}.jpg'
            Image.new('RGB', (100, 60), 'blue').save(path)
            frames.append(path)
        return frames
    monkeypatch.setattr(analysis, 'extract_frames', extract)
    calls = []
    def query(engine, key, model, prompt, image_path=None):
        manifest = json.loads(prompt.split('Reference manifest JSON:\n')[1])
        calls.append(manifest)
        with Image.open(image_path) as image:
            assert image.size == (1440, len(manifest) * 294)
        return {'shots': [{'index': item['index'], 'description': f"shot-{item['index']}", 'redesign_prompt': 'Original forest', 'motion': 'static', 'motion_confidence': 'low'} for item in reversed(manifest)]}
    monkeypatch.setattr(analysis, '_query', query)
    return shots, sampled, calls


def test_batches_keep_all_three_frames_per_shot_and_match_by_id(monkeypatch, tmp_path):
    shots, sampled, calls = batch_fixture(monkeypatch)
    result = montage.analyse('fixture.mp4', tmp_path, 13., 'gemini', 'key', 'fixture-model')
    assert len(calls) == 3  # 6 + 6 + 1, rather than 13 requests.
    assert len(sampled) == 3 * len(shots)
    assert [shot['description'] for shot in result['shots']] == [f'shot-{index}' for index in range(13)]
    assert all(Path(shot['reference_frame']).is_file() for shot in result['shots'])
    montage.analyse('fixture.mp4', tmp_path, 13., 'gemini', 'key', 'fixture-model')
    assert len(calls) == 3 and len(sampled) == 39


def test_incomplete_batch_is_not_applied(monkeypatch, tmp_path):
    batch_fixture(monkeypatch, 2)
    monkeypatch.setattr(analysis, '_query', lambda *args, **kwargs: {'shots': [{'index': 0, 'description': 'a', 'redesign_prompt': 'b', 'motion': 'static'}]})
    with pytest.raises(analysis.SourceAnalysisError, match='неполный пакет'):
        montage.analyse('fixture.mp4', tmp_path, 2., 'gemini', 'key', 'fixture-model')
    assert not list((tmp_path / 'montage').glob('shot_*.json'))
    assert not list((tmp_path / 'montage' / 'responses').glob('*.json'))


def test_committed_batch_recovers_after_missing_individual_files(monkeypatch, tmp_path):
    _, sampled, calls = batch_fixture(monkeypatch, 6)
    result = montage.analyse('fixture.mp4', tmp_path, 6., 'gemini', 'key', 'fixture-model')
    for path in (tmp_path / 'montage').glob('shot_*.json'):
        path.unlink()
    resumed = montage.analyse('fixture.mp4', tmp_path, 6., 'gemini', 'key', 'fixture-model')
    assert result['shots'] == resumed['shots']
    assert len(calls) == 1 and len(sampled) == 18


def test_upgrade_preserves_legacy_individual_results(monkeypatch, tmp_path):
    shots, sampled, calls = batch_fixture(monkeypatch, 2)
    directory = tmp_path / 'montage'
    directory.mkdir()
    reference = directory / 'old-reference.jpg'
    Image.new('RGB', (100, 60), 'red').save(reference)
    identity = {'version': montage.VERSION, 'shot': shots[0], 'engine': 'gemini', 'model': 'fixture-model'}
    record = {'identity': identity, 'shot': {**shots[0], 'description': 'legacy-result', 'redesign_prompt': 'Old prompt', 'motion': 'static', 'reference_frame': str(reference)}}
    (directory / 'shot_00000.json').write_text(json.dumps(record), encoding='utf8')
    result = montage.analyse('fixture.mp4', tmp_path, 2., 'gemini', 'key', 'fixture-model')
    assert result['shots'][0]['description'] == 'legacy-result'
    assert [item['index'] for item in calls[0]] == [1]
    assert len(sampled) == 3


@pytest.mark.parametrize('indexes', [[0, 0], [0, 99], ['0', 1], [False, 1]])
def test_batch_rejects_duplicate_unknown_or_noninteger_ids(monkeypatch, tmp_path, indexes):
    batch_fixture(monkeypatch, 2)
    monkeypatch.setattr(analysis, '_query', lambda *args, **kwargs: {'shots': [{'index': index, 'description': 'desc', 'redesign_prompt': 'prompt', 'motion': 'static'} for index in indexes]})
    with pytest.raises(analysis.SourceAnalysisError, match='ID'):
        montage.analyse('fixture.mp4', tmp_path, 2., 'gemini', 'key', 'fixture-model')
    assert not list((tmp_path / 'montage').glob('shot_*.json'))
