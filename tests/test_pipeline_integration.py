"""Offline workflow tests: service responses are fixtures, media processing is real.

These tests validate orchestration and produced media; they do not assert that any
remote AI service or credential currently works.
"""
import json
from pathlib import Path
import shutil
import subprocess
import threading
from types import SimpleNamespace

import pytest
import requests
from PIL import Image

import pipeline


pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="Real FFmpeg and ffprobe are required for media integration tests",
)


@pytest.fixture
def offline_workflow(monkeypatch, tmp_path):
    """Replace only the external STT, LLM and image/video generation boundary."""
    data = tmp_path / "данные с пробелами"
    data.mkdir()
    monkeypatch.setenv("KADROGEN_DATA_DIR", str(data))
    monkeypatch.setattr(pipeline, "data_dir", lambda: str(data))
    monkeypatch.setattr(pipeline, "W", 320)
    monkeypatch.setattr(pipeline, "H", 180)
    monkeypatch.setattr(pipeline, "load_config", lambda: {"image_providers": ["google"]})
    # Any unexpected network request is a test failure, never a paid API call.
    def reject_network(*args, **kwargs):
        raise AssertionError("Unexpected network request in offline integration test")
    monkeypatch.setattr(requests.sessions.Session, "request", reject_network)
    calls = {"stt": 0, "image": 0, "video": 0}
    control = {"fail_images": False, "cancel_images": False, "duration": 9.0}

    class Speech:
        def transcribe(self, audio_path, progress=None):
            calls["stt"] += 1
            if control["duration"] < 2:
                words = [{"text": "Привет.", "start": 0.1, "end": 0.8}]
            else:
                words = [
                    {"text": "Первый", "start": 0.1, "end": 1.4},
                    {"text": "кадр.", "start": 1.5, "end": 4.2},
                    {"text": "Второй", "start": 4.6, "end": 6.0},
                    {"text": "кадр.", "start": 6.1, "end": 8.7},
                ]
            return {"text": " ".join(w["text"] for w in words), "words": words}

    monkeypatch.setattr(pipeline, "make_text_engine", lambda cfg: ("assemblyai", Speech(), "openai", None))
    monkeypatch.setattr(pipeline, "build_style", lambda *args: {"visual_style": "fixture", "characters": []})

    def prompts(llm, style, scenes, job_dir, progress, use_refs):
        return [{**s, "prompt": f"Landscape scene {s['index']}", "characters": []} for s in scenes]
    monkeypatch.setattr(pipeline, "build_prompts", prompts)

    def image(scene, out_path, progress, refs):
        calls["image"] += 1
        if control["cancel_images"]:
            pipeline.CANCEL.set()
            pipeline.check_cancel()
        if control["fail_images"]:
            return None
        color = (180, 45, 60) if scene["index"] == 0 else (30, 100, 200)
        Image.new("RGB", (640, 360), color).save(out_path)
        return "fixture-image"

    class Video:
        def generate_video_from_image(self, prompt, img_path, out_path, **kwargs):
            calls["video"] += 1
            subprocess.run([
                pipeline.ffmpeg_bin(), "-y", "-loglevel", "error", "-loop", "1",
                "-i", img_path, "-t", "0.5", "-vf", "scale=320:180", "-r", "25",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", out_path,
            ], check=True, capture_output=True, timeout=30)
            return "fixture-video"

    def context(cfg, providers):
        return {
            "api": None, "providers": ["google"], "bases": ["google"], "fallback": [],
            "gates": {"google": SimpleNamespace(threads=1, vsem=threading.Semaphore(1))},
            "image_gen": {"google": image}, "google": Video(),
        }
    monkeypatch.setattr(pipeline, "make_context", context)

    def audio(duration):
        control["duration"] = duration
        path = tmp_path / "Озвучка тест.wav"
        subprocess.run([
            pipeline.ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi",
            "-i", "sine=frequency=440:sample_rate=16000", "-t", str(duration), str(path),
        ], check=True, capture_output=True, timeout=30)
        return str(path)

    pipeline.CANCEL.clear()
    yield SimpleNamespace(data=data, audio=audio, calls=calls, control=control)
    pipeline.CANCEL.clear()


def inspect_media(path):
    result = subprocess.run([
        pipeline.ffmpeg_bin("ffprobe"), "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(path),
    ], check=True, capture_output=True, text=True, timeout=30)
    metadata = json.loads(result.stdout)
    # Decode every stream to prove the output is a readable video, not just a container.
    subprocess.run([
        pipeline.ffmpeg_bin(), "-v", "error", "-i", str(path), "-f", "null", "-",
    ], check=True, capture_output=True, timeout=30)
    return metadata


@pytest.mark.parametrize("mode,motion,subtitles,duration,size", [
    ("images", False, False, 1.2, (1920, 1080)),
    ("images", True, True, 9.0, (320, 180)),
    ("mixed", False, False, 9.0, (320, 180)),
])
def test_audio_to_final_video(offline_workflow, monkeypatch, mode, motion, subtitles, duration, size):
    wf = offline_workflow
    monkeypatch.setattr(pipeline, "W", size[0])
    monkeypatch.setattr(pipeline, "H", size[1])
    audio = wf.audio(duration)
    events = []
    # Preparation and production share the real transcript/scene cache.
    estimate = pipeline.prepare(audio, provider="google")
    out = Path(pipeline.make_video(
        audio, provider="google", mode=mode, motion=motion, subtitles=subtitles,
        progress_cb=lambda stage, *args: events.append(stage),
    ))
    result = inspect_media(out)
    video = next(s for s in result["streams"] if s["codec_type"] == "video")
    sound = next(s for s in result["streams"] if s["codec_type"] == "audio")
    assert (video["width"], video["height"]) == size
    assert video["codec_name"] == "h264"
    assert sound["codec_name"] == "aac"
    assert abs(float(result["format"]["duration"]) - duration) < 0.3
    assert events[-1] == "done"
    assert {"start", "transcribe", "scenes", "images", "render"} <= set(events)
    assert estimate["scenes"] == (1 if duration < 2 else 2)
    assert wf.calls["stt"] == 1
    assert wf.calls["image"] == estimate["scenes"]
    assert wf.calls["video"] == (1 if mode == "mixed" else 0)
    if subtitles:
        srt = out.parent / "subtitles" / (out.stem + ".srt")
        assert "Первый" in srt.read_text(encoding="utf-8-sig")
        assert "subtitles" in events
        assert not out.with_suffix(".srt").exists()
    assert not list(out.parent.glob("*.tmp.mp4"))


def test_all_image_generation_failures_do_not_report_success(offline_workflow):
    wf = offline_workflow
    wf.control["fail_images"] = True
    events = []
    with pytest.raises(RuntimeError, match="ни одной картинки"):
        pipeline.make_video(wf.audio(1.2), provider="google", progress_cb=lambda s, *a: events.append(s))
    assert "done" not in events
    assert wf.calls["image"] == 2  # initial pass plus the documented retry
    assert not list((wf.data / "output").glob("*.mp4"))


def test_stop_during_image_generation_does_not_render_output(offline_workflow):
    wf = offline_workflow
    wf.control["cancel_images"] = True
    events = []
    with pytest.raises(pipeline.Cancelled):
        pipeline.make_video(wf.audio(1.2), provider="google", progress_cb=lambda s, *a: events.append(s))
    assert "done" not in events
    assert "render" not in events
    assert not list((wf.data / "output").glob("*.mp4"))
