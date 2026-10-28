import json
from pathlib import Path
from unittest.mock import Mock

import pytest

import netutil
import pipeline
import source_analysis as analysis
from media_tools import ffmpeg_bin, run_media


STYLE = {"visual_style": {"description": "blue backgrounds", "palette": ["blue"],
                         "composition": "centered", "shot_types": ["wide"]},
         "narrative_style": "chronological"}


@pytest.fixture(autouse=True)
def cancellation_reset(monkeypatch):
    monkeypatch.setattr(netutil, "CANCEL_CHECK", None)
    pipeline.CANCEL.clear()


def test_sampling_covers_entire_duration_and_cap():
    times = analysis.sample_times(7200)
    assert times[0] == 0
    assert times[-1] > 7199
    assert len(times) == 120
    assert times == sorted(times)


def test_real_video_sampling_full_transcript_and_cached_analysis(monkeypatch, tmp_path):
    video = tmp_path / "source.mp4"
    r = run_media([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                   "color=c=blue:s=320x180:r=25:d=2", "-c:v", "libx264", str(video)])
    assert r.returncode == 0
    monkeypatch.setattr(analysis, "_preflight", lambda *a: None)
    calls = []
    def query(engine, key, model, prompt, image_path=None):
        calls.append((prompt, image_path))
        if image_path:
            assert Path(image_path).is_file()
        return STYLE.copy()
    monkeypatch.setattr(analysis, "_query", query)
    transcript = "first fact. " * 1400 + "LAST_FACT"
    cfg = {"fastgen_api_key": "fixture"}
    result = analysis.analyse(str(video), transcript, cfg, str(tmp_path))
    assert result["sampling"]["frame_count"] == 3
    assert result["sampling"]["full_transcript_characters"] == len(transcript)
    assert result["sampling"]["timestamps"][-1] >= 1.9
    assert any("LAST_FACT" in prompt for prompt, image in calls if image is None)
    assert any(image for prompt, image in calls)
    count = len(calls)
    assert analysis.analyse(str(video), transcript, cfg, str(tmp_path)) == result
    assert len(calls) == count


def test_fastgen_multimodal_access_required(monkeypatch):
    monkeypatch.setattr(analysis, "_http", lambda *a, **k: {"capabilities": {"chat.multimodal": False}})
    with pytest.raises(analysis.SourceAnalysisError, match="chat.multimodal"):
        analysis._preflight("fastgen", "fixture")


def test_fastgen_vision_sends_image_payload(monkeypatch, tmp_path):
    image = tmp_path / "image.jpg"
    image.write_bytes(b"jpeg fixture")
    http = Mock(return_value={"choices": [{"message": {"content": json.dumps(STYLE)}, "finish_reason": "stop"}]})
    monkeypatch.setattr(analysis, "_http", http)
    assert analysis._query("fastgen", "fixture", "google/gemini-3.6-flash", "analyse", image) == STYLE
    payload = http.call_args.kwargs["json"]
    assert payload["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_bad_analysis_json_and_truncation_rejected(monkeypatch):
    for content, reason in [("not json", "stop"), (json.dumps(STYLE), "length")]:
        monkeypatch.setattr(analysis, "_http", lambda *a, **k: {"choices": [{"message": {"content": content}, "finish_reason": reason}]})
        with pytest.raises(analysis.SourceAnalysisError):
            analysis._query("fastgen", "fixture", "google/gemini-3.6-flash", "analyse")


def test_cancelled_analysis_does_not_request_provider(monkeypatch, tmp_path):
    monkeypatch.setattr(netutil, "CANCEL_CHECK", Mock(side_effect=pipeline.Cancelled()))
    provider = Mock()
    monkeypatch.setattr(analysis, "_provider", provider)
    with pytest.raises(pipeline.Cancelled):
        analysis.analyse("unused.mp4", "text", {}, str(tmp_path))
    provider.assert_not_called()
