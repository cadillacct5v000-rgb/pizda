import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from PIL import Image

import app
import elysium_video as ev
import netutil
import pipeline
from native_projects import ProjectStore


def response(body, status=200):
    return SimpleNamespace(status_code=status, json=lambda: body)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    pipeline.CANCEL.clear()
    monkeypatch.setattr(netutil, 'CANCEL_CHECK', None)
    monkeypatch.setattr(netutil, 'pause', lambda seconds: None)
    image = tmp_path / 'frame.png'
    Image.new('RGB', (32, 18), 'red').save(image)
    Path(str(image) + '.source.json').write_text(json.dumps({
        'url': 'https://cdn.example/frame.png', 'sha256': hashlib.sha256(image.read_bytes()).hexdigest()}))
    session = Mock(headers={})
    store = ProjectStore(tmp_path / 'state')
    client = ev.ElysiumVideo('test-secret', store=store, session=session, poll=0)
    monkeypatch.setattr(requests, 'get', Mock(side_effect=AssertionError('Unexpected download')))
    yield client, session, store, image, tmp_path
    pipeline.CANCEL.clear()


def test_check_does_not_generate(setup):
    client, session, *_ = setup
    session.get.side_effect = [response({'data': []}), response({'data': [
        {'id': ev.MODEL, 'operations': [{'name': 'generate'}]}]})]
    assert 'не запускалась' in client.check()
    session.post.assert_not_called()


def test_timeout_is_not_resubmitted_even_after_restart(setup):
    client, session, store, image, root = setup
    session.post.side_effect = requests.Timeout('SECRET must not be printed')
    with pytest.raises(ev.ElysiumVideoError, match='Автоповтора нет') as error:
        client.generate('wave', image, root / 'out.mp4', 4)
    assert 'SECRET' not in str(error.value)
    restarted = ev.ElysiumVideo('test-secret', store=store, session=session)
    with pytest.raises(ev.ElysiumVideoError, match='Отправка видео'):
        restarted.generate('wave', image, root / 'out.mp4', 4)
    assert session.post.call_count == 1


def test_resume_known_task_does_not_generate_twice(setup):
    client, session, _, image, root = setup
    session.post.return_value = response({'id': 'video_123', 'status': 'queued'})
    session.get.side_effect = requests.ConnectionError('offline')
    with pytest.raises(requests.ConnectionError):
        client.generate('wave', image, root / 'out.mp4', 4.2)
    session.get.side_effect = None
    session.get.return_value = response({'status': 'failed'})
    with pytest.raises(ev.ElysiumVideoError, match='не создал'):
        client.generate('wave', image, root / 'out.mp4', 4.2)
    assert session.post.call_count == 1
    body = session.post.call_args.kwargs['json']
    assert body['model'] == ev.MODEL and body['operation'] == 'generate'
    assert body['input']['first_frame_url'] == 'https://cdn.example/frame.png'
    assert body['input']['duration'] == 5


@pytest.mark.parametrize('status', [400, 401, 402, 403, 429])
def test_rejected_post_keeps_error_code_without_auto_retry(setup, status):
    client, session, _, image, root = setup
    session.post.return_value = response({'error': 'test-secret'}, status)
    with pytest.raises(ev.ElysiumVideoError, match=str(status)) as error:
        client.generate('wave', image, root / 'out.mp4', 5)
    assert 'test-secret' not in str(error.value)
    assert session.post.call_count == 1


def test_missing_source_url_stops_before_submission(setup):
    client, session, _, image, root = setup
    Path(str(image) + '.source.json').unlink()
    with pytest.raises(ev.ElysiumVideoError, match='исходной HTTPS'):
        client.generate('wave', image, root / 'out.mp4', 5)
    session.post.assert_not_called()


def test_changed_image_cannot_reuse_old_url(setup):
    client, session, _, image, root = setup
    Image.new('RGB', (32, 18), 'blue').save(image)
    with pytest.raises(ev.ElysiumVideoError, match='исходной HTTPS'):
        client.generate('wave', image, root / 'out.mp4', 5)
    session.post.assert_not_called()


def test_completed_download_has_no_provider_auth_and_keeps_task(setup, monkeypatch):
    client, session, _, image, root = setup
    session.post.return_value = response({'id': 'video_123'})
    session.get.return_value = response({'status': 'completed', 'metadata': {'url': 'https://cdn.example/result.mp4'}})
    download = Mock()
    download.__enter__ = Mock(return_value=download)
    download.__exit__ = Mock(return_value=False)
    get = Mock(return_value=download)
    monkeypatch.setattr(requests, 'get', get)
    monkeypatch.setattr(netutil, 'save_response', lambda r, p: Path(p).write_bytes(b'fixture'))
    monkeypatch.setattr('media_tools.run_media', lambda args: SimpleNamespace(returncode=0, stdout=b'video\n'))
    out = root / 'out.mp4'
    assert client.generate('wave', image, out, 5) == 'elysium:' + ev.MODEL
    assert out.read_bytes() == b'fixture'
    assert 'headers' not in get.call_args.kwargs
    # A lost local download is recovered by polling the original task.
    out.unlink()
    client.generate('wave', image, out, 5)
    assert session.post.call_count == 1


def test_elysium_video_bypasses_fastgen_video_gate(setup, monkeypatch):
    _, _, _, image, root = setup
    image_dir = root / 'images'; image_dir.mkdir()
    (image_dir / '0000.png').write_bytes(image.read_bytes())
    generate = Mock(side_effect=lambda prompt, img, out, duration, progress: (Path(out).write_bytes(b'video'), 'ok')[1])
    monkeypatch.setattr(ev.ElysiumVideo, 'generate', generate)
    monkeypatch.setattr(pipeline, 'RateGate', Mock(side_effect=AssertionError('FastGen video gate must not be used')))
    scene = {'index': 0, 'duration': 5, 'prompt': 'drawing', 'motion': 'wave'}
    context = {'bases': ['fastgen'], 'video_engine': 'elysium', 'video_identity': 'test',
               'video_config': {'elysium_api_key': 'test-secret'}}
    made = pipeline.generate_all_videos(context, [scene], str(image_dir), str(root), lambda *a: None)
    assert 0 in made and 'videos_test' in made[0]
    generate.assert_called_once()


def test_preflight_ignores_fastgen_video_denial_for_elysium(monkeypatch):
    monkeypatch.setattr(app, 'load_cfg', lambda: {'video_engine': 'elysium', 'elysium_api_key': 'test', 'fastgen_api_key': 'test'})
    monkeypatch.setattr(app, 'estimate_plan', lambda *a, **k: {'clips': 3})
    monkeypatch.setattr(app.FastGen, 'usage', Mock(side_effect=AssertionError('Must not probe FastGen video')))
    assert app.production_check({'providers': ['fastgen'], 'mode': 'video'})['ok']


def test_fastgen_image_retains_reference_url_and_hash(setup, monkeypatch):
    import threading
    from shutil import copyfile
    _, _, _, image, root = setup
    monkeypatch.setattr(pipeline, 'HD_IMAGES', False)
    api = Mock()
    api.run.return_value = {'status': 'succeeded', 'results': [{'download_url': 'https://cdn.example/new.png'}]}
    api.download_result.side_effect = lambda item, path: copyfile(image, path)
    gate = SimpleNamespace(lock=threading.Lock(), sem=threading.Semaphore(1), disabled=False,
                           unavailable_ops={}, hour_limit=0, acquire=Mock(), request_done=Mock())
    output = root / 'new.png'
    assert pipeline.gen_fastgen_image(api, gate, {'index': 0, 'prompt': 'drawing'}, str(output), Mock(), [],
                                     chain=[('gemini_nano_banana_2_image_generate', 1)])
    reference = json.loads(Path(str(output) + '.source.json').read_text())
    assert reference['url'] == 'https://cdn.example/new.png'
    assert reference['sha256'] == hashlib.sha256(output.read_bytes()).hexdigest()


def test_real_mp4_download_and_probe(setup, monkeypatch):
    import subprocess
    from media_tools import ffmpeg_bin
    client, session, _, image, root = setup
    fixture = root / 'fixture.mp4'
    subprocess.run([ffmpeg_bin(), '-v', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=160x90:d=0.2',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(fixture)], check=True, timeout=30)
    download = Mock()
    download.__enter__ = Mock(return_value=download)
    download.__exit__ = Mock(return_value=False)
    download.iter_content.return_value = [fixture.read_bytes()]
    monkeypatch.setattr(requests, 'get', Mock(return_value=download))
    session.post.return_value = response({'id': 'real_fixture'})
    session.get.return_value = response({'status': 'completed', 'metadata': {'url': 'https://cdn.example/real.mp4'}})
    output = root / 'real.mp4'
    client.generate('wave', image, output, 3)
    assert output.read_bytes() == fixture.read_bytes()
