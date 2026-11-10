import json
import subprocess

import pytest
from PIL import Image

import pipeline


@pytest.mark.parametrize('value', [25, 30, 60, '60'])
def test_supported_frame_rates(value):
    assert pipeline.validate_fps(value) == int(value)


@pytest.mark.parametrize('value', [True, False, 0, 24, 120, '60.0', None, 'bad'])
def test_reject_invalid_frame_rates(value):
    with pytest.raises(ValueError):
        pipeline.validate_fps(value)


def test_actual_60fps_render_invalidates_30fps_clip(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, 'W', 320)
    monkeypatch.setattr(pipeline, 'H', 180)
    monkeypatch.setattr(pipeline, 'FPS', 30)
    pipeline.CANCEL.clear()
    image = tmp_path / 'frame.png'
    Image.new('RGB', (400, 240), '#234455').save(image)
    target = str(tmp_path / 'clip.mp4')
    pipeline.render_scene_clip(str(image), 0, .4, target)
    monkeypatch.setattr(pipeline, 'FPS', 60)
    pipeline.render_scene_clip(str(image), 0, .4, target)
    from media_tools import ffmpeg_bin
    ffprobe = ffmpeg_bin('ffprobe')
    result = subprocess.run([ffprobe, '-v', 'error', '-show_streams', '-of', 'json', target], capture_output=True, text=True, check=True)
    video = json.loads(result.stdout)['streams'][0]
    assert video['r_frame_rate'] == '60/1'
    assert int(video['nb_frames']) == 24
