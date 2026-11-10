import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

import netutil
import source_analysis as analysis

STYLE = {'visual_style': {'description': 'blue', 'palette': ['blue'], 'composition': 'wide', 'shot_types': ['wide']}, 'narrative_style': 'chronological'}


@pytest.fixture(autouse=True)
def no_cancel(monkeypatch):
    monkeypatch.setattr(netutil, 'CANCEL_CHECK', None)


class Response:
    def __init__(self, status, payload=None, retry_after=None):
        self.status_code = status
        self.payload = payload or {}
        self.headers = {} if retry_after is None else {'Retry-After': retry_after}
        self.closed = False

    def json(self):
        return self.payload

    def close(self):
        self.closed = True


def test_503_retries_with_retry_after_and_reports_progress(monkeypatch):
    responses = [Response(503, retry_after='7'), Response(503), Response(200, {'ok': True})]
    request = Mock(side_effect=responses)
    monkeypatch.setattr(analysis.requests, 'request', request)
    pauses = []
    monkeypatch.setattr(netutil, 'pause', pauses.append)
    messages = []
    token = analysis._REQUEST_PROGRESS.set(messages.append)
    try:
        assert analysis._http('POST', 'https://example.org/analysis') == {'ok': True}
    finally:
        analysis._REQUEST_PROGRESS.reset(token)
    assert pauses == [7, 10]
    assert request.call_count == 3
    assert len(messages) == 2 and '503' in messages[0]
    assert all(response.closed for response in responses)


def test_long_retry_after_does_not_retry_early(monkeypatch):
    request = Mock(return_value=Response(429, retry_after='3600'))
    monkeypatch.setattr(analysis.requests, 'request', request)
    pause = Mock()
    monkeypatch.setattr(netutil, 'pause', pause)
    with pytest.raises(analysis.SourceAnalysisError, match='3600'):
        analysis._http('POST', 'https://example.org/analysis')
    assert request.call_count == 1
    pause.assert_not_called()


def test_503_has_deadline_and_preserves_http_status(monkeypatch):
    request = Mock(side_effect=lambda *args, **kwargs: Response(503))
    monkeypatch.setattr(analysis.requests, 'request', request)
    pauses = []
    monkeypatch.setattr(netutil, 'pause', pauses.append)
    with pytest.raises(analysis.SourceAnalysisError, match='503') as caught:
        analysis._http('POST', 'https://example.org/analysis')
    assert request.call_count == 5
    assert pauses == [5, 10, 20, 40]
    assert caught.value.status == 503


def test_lost_billable_post_response_is_not_automatically_repeated(monkeypatch):
    request = Mock(side_effect=requests.ReadTimeout('response lost'))
    monkeypatch.setattr(analysis.requests, 'request', request)
    pause = Mock()
    monkeypatch.setattr(netutil, 'pause', pause)
    with pytest.raises(analysis.SourceAnalysisError, match='списание.*неизвестны'):
        analysis._http('POST', 'https://example.org/analysis')
    assert request.call_count == 1
    pause.assert_not_called()


def test_validated_responses_resume_after_later_503(monkeypatch, tmp_path):
    calls = []
    fail = True
    def query(engine, key, model, prompt, image_path=None):
        calls.append(prompt)
        if prompt == 'second' and fail:
            raise analysis.SourceAnalysisError('HTTP 503', 503)
        return STYLE
    monkeypatch.setattr(analysis, '_query', query)
    first = analysis.cached_query(tmp_path, 'gemini', 'not-stored-key', 'test-model', 'first', validate=analysis._style)
    with pytest.raises(analysis.SourceAnalysisError, match='сохранены'):
        analysis.cached_query(tmp_path, 'gemini', 'not-stored-key', 'test-model', 'second', validate=analysis._style)
    fail = False
    assert analysis.cached_query(tmp_path, 'gemini', 'not-stored-key', 'test-model', 'first', validate=analysis._style) == first
    analysis.cached_query(tmp_path, 'gemini', 'not-stored-key', 'test-model', 'second', validate=analysis._style)
    assert calls == ['first', 'second', 'second']
    assert all('not-stored-key' not in path.read_text() for path in tmp_path.rglob('*.json'))


def test_invalid_response_not_cached_and_changed_image_invalidates(monkeypatch, tmp_path):
    query = Mock(side_effect=[{'bad': True}, STYLE, STYLE])
    monkeypatch.setattr(analysis, '_query', query)
    image = tmp_path / 'image.jpg'
    image.write_bytes(b'first-image')
    with pytest.raises(analysis.SourceAnalysisError):
        analysis.cached_query(tmp_path, 'gemini', 'key', 'model', 'prompt', image, validate=analysis._style)
    assert not list((tmp_path / 'responses').glob('*.json'))
    analysis.cached_query(tmp_path, 'gemini', 'key', 'model', 'prompt', image, validate=analysis._style)
    analysis.cached_query(tmp_path, 'gemini', 'key', 'model', 'prompt', image, validate=analysis._style)
    assert query.call_count == 2
    image.write_bytes(b'changed-image')
    analysis.cached_query(tmp_path, 'gemini', 'key', 'model', 'prompt', image, validate=analysis._style)
    assert query.call_count == 3


def test_whole_video_retry_skips_completed_visual_batch(monkeypatch, tmp_path):
    from PIL import Image
    video = tmp_path / 'source.mp4'
    video.write_bytes(b'source-fixture')
    monkeypatch.setattr(analysis, '_duration', lambda path: 458.)
    def extract(path, directory, timestamps):
        Path(directory).mkdir(parents=True, exist_ok=True)
        result = []
        for index, _ in enumerate(timestamps):
            image = Path(directory) / f'frame_{index}.jpg'
            Image.new('RGB', (100, 60), 'blue').save(image)
            result.append(image)
        return result
    monkeypatch.setattr(analysis, 'extract_frames', extract)
    calls = []
    outage = True
    def query(engine, key, model, prompt, image_path=None):
        nonlocal outage
        label = Path(image_path).name if image_path else 'text'
        calls.append(label)
        if label == 'sheet_001.jpg' and outage:
            outage = False
            raise analysis.SourceAnalysisError('HTTP 503', 503)
        return STYLE
    monkeypatch.setattr(analysis, '_query', query)
    cfg = {'source_analysis_engine': 'gemini', 'gemini_api_key': 'fixture'}
    with pytest.raises(analysis.SourceAnalysisError, match='503'):
        analysis.analyse(video, 'Narration.', cfg, tmp_path / 'cache')
    result = analysis.analyse(video, 'Narration.', cfg, tmp_path / 'cache')
    assert result['sampling']['frame_count'] == 17
    assert calls.count('sheet_000.jpg') == 1
    assert calls.count('sheet_001.jpg') == 2
    assert calls.count('sheet_002.jpg') == 1
