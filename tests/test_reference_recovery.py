import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image
import pytest

import elysium_video as ev
import netutil
import storyboard_cache as cache
from native_projects import ProjectStore


def frame(path, color='red'):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (32, 18), color).save(path)
    return path


def receipt(path, fingerprint=None, url='https://cdn.example/frame.png'):
    note = {'sha256': fingerprint or hashlib.sha256(path.read_bytes()).hexdigest(), 'url': url}
    Path(str(path) + '.source.json').write_text(json.dumps(note), encoding='utf-8')
    return note


def test_existing_project_png_recovers_cached_receipt(tmp_path):
    scene = {'index': 0, 'plan_id': 'plan', 'prompt': 'drawing', 'duration': 5}
    image = frame(tmp_path / 'job/images/0000.png')
    cached = frame(tmp_path / 'assets' / ('i_' + cache.image_key(scene) + '.png'))
    note = receipt(cached)
    original = image.read_bytes()
    cache.restore([scene], tmp_path / 'job', tmp_path / 'assets')
    assert image.read_bytes() == original
    assert cache.source_reference(image) == note


def test_publish_repairs_existing_cached_receipt(tmp_path):
    scene = {'index': 0, 'plan_id': 'plan', 'prompt': 'drawing', 'duration': 5}
    image = frame(tmp_path / 'job/images/0000.png')
    note = receipt(image)
    cached = frame(tmp_path / 'assets' / ('i_' + cache.image_key(scene) + '.png'))
    cache.publish([scene], tmp_path / 'job', tmp_path / 'assets')
    assert cache.source_reference(cached) == note


@pytest.mark.parametrize('kind', ['wrong_hash', 'http', 'malformed'])
def test_recovery_does_not_attach_unrelated_or_invalid_reference(tmp_path, kind):
    image = frame(tmp_path / 'frame.png')
    candidate = frame(tmp_path / 'other.png', 'blue')
    if kind == 'wrong_hash':
        receipt(candidate)
    elif kind == 'http':
        receipt(candidate, hashlib.sha256(image.read_bytes()).hexdigest(), 'http://cdn.example/frame.png')
    else:
        Path(str(candidate) + '.source.json').write_text('[]')
    assert not cache.restore_source(image, [Path(str(candidate) + '.source.json')])
    assert not Path(str(image) + '.source.json').exists()


def test_kling_recovers_exact_image_url_from_other_job_without_redrawing(tmp_path, monkeypatch):
    image = frame(tmp_path / 'current/frame.png')
    original = image.read_bytes()
    duplicate = frame(tmp_path / 'jobs/old/images/0000.png')
    note = receipt(duplicate)
    frame(tmp_path / 'jobs/unrelated.png', 'blue')
    monkeypatch.setattr(ev, 'data_dir', lambda: str(tmp_path))
    monkeypatch.setattr(netutil, 'CANCEL_CHECK', None)
    session = Mock(headers={})
    session.post.return_value = SimpleNamespace(status_code=200, json=lambda: {'id': 'task_recovered'})
    session.get.return_value = SimpleNamespace(status_code=200, json=lambda: {'status': 'failed', 'error': 'fixture'})
    client = ev.ElysiumVideo('fixture', model=ev.KLING_MODEL, session=session, store=ProjectStore(tmp_path / 'db'))
    with pytest.raises(ev.ElysiumVideoError, match='не создал'):
        client.generate('wave', image, tmp_path / 'out.mp4', 5)
    assert session.post.call_count == 1
    assert session.post.call_args.kwargs['json']['input']['image_urls'] == [note['url']]
    assert image.read_bytes() == original
    assert cache.source_reference(image) == note
