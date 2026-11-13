import hashlib
import json
import time
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests
from PIL import Image

import elysium_video as ev
import netutil
from native_projects import ProjectStore
from public_images import PublicImages, PublicImageError, valid_url


def reply(status=200, body=None, data=b''):
    result = Mock(status_code=status)
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    result.json.return_value = body
    result.iter_content.return_value = [data]
    return result


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(netutil, 'CANCEL_CHECK', None)
    image = tmp_path / 'private_project_name.png'
    Image.new('RGB', (32, 18), 'blue').save(image)
    store = ProjectStore(tmp_path / 'db')
    post = Mock(return_value=reply(body={'success': True, 'files': [{'url': 'https://h.uguu.se/fixture.png'}]}))
    get = Mock(return_value=reply(data=image.read_bytes()))
    monkeypatch.setattr(requests, 'post', post)
    monkeypatch.setattr(requests, 'get', get)
    return PublicImages(store), image, post, get, store


def test_upload_uses_generic_filename_and_public_get_without_secrets(storage):
    host, image, post, get, _ = storage
    original = image.read_bytes()
    assert host.ensure(image) == 'https://h.uguu.se/fixture.png'
    file = post.call_args.kwargs['files']['files[]']
    assert file == ('frame.png', original, 'image/png')
    assert 'headers' not in post.call_args.kwargs and 'headers' not in get.call_args.kwargs
    assert get.call_args.kwargs['allow_redirects'] is False
    assert image.read_bytes() == original
    host.ensure(image)
    assert post.call_count == 1 and get.call_count == 2


def test_expired_reference_is_reuploaded(storage):
    host, image, post, get, store = storage
    host.ensure(image)
    key = 'public-image:uguu:' + hashlib.sha256(image.read_bytes()).hexdigest()
    saved = store.operation(key)
    store.operation(key, {**saved, 'expires_at': time.time() - 1})
    host.ensure(image)
    assert post.call_count == 2


@pytest.mark.parametrize('status,data', [(404, b''), (200, b'<html>not the image</html>')])
def test_unavailable_or_changed_download_never_returns_reference(storage, status, data):
    host, image, post, get, store = storage
    get.return_value = reply(status=status, data=data)
    with pytest.raises(PublicImageError, match='Видеозадание не отправлено'):
        host.ensure(image)
    assert store.operation('public-image:uguu:' + hashlib.sha256(image.read_bytes()).hexdigest()) is None


@pytest.mark.parametrize('url', ['http://h.uguu.se/a.png', 'https://uguu.se.evil.test/a.png',
                              'https://user:secret@h.uguu.se/a.png', 'https://127.0.0.1/a.png',
                              'https://h.uguu.se:444/a.png', 'https://h.uguu.se/a.png?token=x'])
def test_host_urls_are_strictly_validated(url):
    assert not valid_url(url)


def test_hailuo_upload_failure_never_submits_paid_job(storage):
    host, image, post, get, store = storage
    post.side_effect = requests.Timeout('private upload detail')
    session = Mock(headers={})
    client = ev.ElysiumVideo('test-secret', model=ev.HAILUO_MODEL, session=session, store=store)
    with pytest.raises(ev.ElysiumVideoError, match='Видеозадание не отправлено'):
        client.generate('wave', image, image.parent / 'out.mp4', 3)
    session.post.assert_not_called()


def test_known_hailuo_task_resumes_without_upload(storage):
    host, image, post, get, store = storage
    session = Mock(headers={})
    session.post.return_value = Mock(status_code=200, json=lambda: {'id': 'existing_task'})
    session.get.return_value = Mock(status_code=200, json=lambda: {'status': 'failed', 'error': 'fixture'})
    client = ev.ElysiumVideo('test-secret', model=ev.HAILUO_MODEL, session=session, store=store)
    with pytest.raises(ev.ElysiumVideoError, match='fixture'):
        client.generate('wave', image, image.parent / 'out.mp4', 3)
    assert session.post.call_args.kwargs['json']['input']['image'] == 'https://h.uguu.se/fixture.png'
    post.reset_mock(); get.reset_mock()
    with pytest.raises(ev.ElysiumVideoError, match='fixture'):
        client.generate('wave', image, image.parent / 'out.mp4', 3)
    post.assert_not_called(); get.assert_not_called()
    assert session.post.call_count == 1


@pytest.mark.parametrize('status', ['submitting', 'uncertain'])
def test_209_uncertain_task_cannot_be_bypassed_by_transport_fix(storage, status):
    host, image, post, get, store = storage
    account = hashlib.sha256(b'test-secret').hexdigest()
    identity = [ev.DEFAULT_BASE, account, ev.HAILUO_MODEL, '768p-inline-v1', 6, 'wave',
                hashlib.sha256(image.read_bytes()).hexdigest()]
    key = 'elysium-video:' + hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    store.operation(key, {'status': status})
    session = Mock(headers={})
    client = ev.ElysiumVideo('test-secret', model=ev.HAILUO_MODEL, session=session, store=store)
    with pytest.raises(ev.ElysiumVideoError, match='Отправка видео'):
        client.generate('wave', image, image.parent / 'out.mp4', 3)
    post.assert_not_called(); get.assert_not_called(); session.post.assert_not_called()


def test_209_rejected_inline_request_can_submit_https_same_receipt(storage):
    host, image, post, get, store = storage
    identity = [ev.DEFAULT_BASE, hashlib.sha256(b'test-secret').hexdigest(), ev.HAILUO_MODEL,
                '768p-inline-v1', 6, 'wave', hashlib.sha256(image.read_bytes()).hexdigest()]
    key = 'elysium-video:' + hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    store.operation(key, {'status': 'rejected'})
    session = Mock(headers={})
    session.post.return_value = Mock(status_code=200, json=lambda: {'id': 'new_https_task'})
    session.get.return_value = Mock(status_code=200, json=lambda: {'status': 'failed', 'error': 'fixture'})
    with pytest.raises(ev.ElysiumVideoError, match='fixture'):
        ev.ElysiumVideo('test-secret', model=ev.HAILUO_MODEL, session=session, store=store).generate('wave', image, image.parent / 'out.mp4', 3)
    assert session.post.call_count == 1 and store.operation(key)['id'] == 'new_https_task'
