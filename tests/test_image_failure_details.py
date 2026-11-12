import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image
import pytest
import pipeline


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    pipeline.CANCEL.clear()
    pipeline.take_redo()
    monkeypatch.setattr(pipeline, 'LOG_PATH', str(tmp_path / 'log.txt'))
    monkeypatch.setattr(pipeline, 'warn_hours', lambda *a: None)
    monkeypatch.setattr(pipeline, 'QA_STATS', dict.fromkeys(pipeline.QA_STATS, 0))
    yield
    pipeline.CANCEL.clear()


def context(draw, qa=None):
    return {'providers': ['fastgen'], 'bases': ['fastgen'], 'fallback': [], 'api': None,
            'image_gen': {'fastgen': draw}, 'gates': {'fastgen': SimpleNamespace(threads=1)}, 'qa': qa}


def test_filter_reason_has_display_number_and_is_saved(tmp_path):
    def draw(scene, path, *args):
        if scene['index'] == 7:
            raise pipeline.SceneRejected('Сцена 8: сервис отклонил содержание.')
        Image.new('RGB', (32, 18), 'red').save(path)
        return 'fixture'
    scenes = [{'index': i, 'prompt': 'drawing', 'characters': []} for i in (0, 7)]
    progress = Mock()
    with pytest.raises(RuntimeError, match='Сцены 8:.*generation.content_policy'):
        pipeline.generate_all_images(context(draw), scenes, {}, str(tmp_path), progress, {})
    state = json.loads((tmp_path / 'images.json').read_text())
    assert state['7']['blocked'] and 'отклонил' in state['7']['error']
    assert (tmp_path / 'images' / '0000.png').exists()
    assert any('Сцена 8' in str(call) for call in progress.call_args_list)


def test_all_images_blocked_is_not_reported_as_key_error(tmp_path):
    draw = Mock(side_effect=pipeline.SceneRejected('Сцена 1: отклонено фильтром'))
    with pytest.raises(RuntimeError, match='generation.content_policy'):
        pipeline.generate_all_images(context(draw), [{'index': 0, 'prompt': 'drawing'}], {}, str(tmp_path), Mock(), {})
    assert draw.call_count == 1


def test_best_qa_image_keeps_its_own_source_url(tmp_path, monkeypatch):
    calls = []
    def draw(scene, path, *args):
        calls.append(path)
        Image.new('RGB', (32, 18), 'red' if len(calls) == 1 else 'blue').save(path)
        Path(path + '.source.json').write_text(json.dumps({'url': 'https://cdn.example/' + str(len(calls)),
            'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()}))
        return 'fixture'
    scores = iter([(False, 9, 'fixture'), (False, 2, 'fixture'), (False, 1, 'fixture')])
    monkeypatch.setattr(pipeline, 'qa_check', lambda *a: next(scores))
    pipeline.generate_all_images(context(draw, object()), [{'index': 0, 'prompt': 'drawing'}], {}, str(tmp_path), Mock(), {})
    path = tmp_path / 'images' / '0000.png'
    note = json.loads(Path(str(path) + '.source.json').read_text())
    assert note['url'] == 'https://cdn.example/1'
    assert note['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert not list(path.parent.glob('*.qa*.source.json'))


def test_video_waits_for_image_qa_to_finish(tmp_path, monkeypatch):
    image_dir = tmp_path / 'images'; image_dir.mkdir()
    Image.new('RGB', (32, 18), 'red').save(image_dir / '0000.png')
    pipeline.write_json(str(tmp_path / 'images.json'), {'0': {'ok': False}})
    def video(ctx, scenes, images, job, progress, mode, first, image_ready, quiet):
        # PNG alone, before the final QA state, is not ready for a paid video.
        assert image_ready(scenes[0]) is False
        pipeline.write_json(str(tmp_path / 'images.json'), {'0': {'ok': True}})
        assert image_ready(scenes[0]) is True
        return {}
    monkeypatch.setattr(pipeline, 'generate_all_videos', video)
    overlap = pipeline.VideoOverlap({}, [{'index': 0}], str(tmp_path), Mock(), 'video', 0)
    overlap.fut.result(timeout=5)
    overlap.result()


def test_redraw_invalidates_elysium_video(tmp_path):
    directory = tmp_path / 'videos_provider'; directory.mkdir()
    clip = directory / '0007.mp4'; clip.write_bytes(b'fixture')
    pipeline.redo_files(str(tmp_path), 7)
    assert not clip.exists()
