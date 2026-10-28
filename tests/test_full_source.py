import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

import app
import faithful_rewrite
import netutil
import pipeline
import rewrite
import source_analysis
import youtube_source


PROFILE = {
    "visual_style": {"description": "Плоская рисованная анимация", "palette": ["синий"],
                     "composition": "крупный предмет в центре", "shot_types": ["общий"]},
    "narrative_style": "Хронологический рассказ с примерами",
    "segments": [{"start": 0, "end": 2, "description": "синие рисунки"}],
    "sampling": {"duration": 2, "frame_count": 3},
}


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(netutil, "CANCEL_CHECK", None)
    monkeypatch.setattr(requests.sessions.Session, "request", Mock(side_effect=AssertionError("Unexpected HTTP")))
    pipeline.CANCEL.clear()
    yield
    pipeline.CANCEL.clear()


def test_full_rewrite_covers_source_in_order_and_resumes(monkeypatch, tmp_path):
    source = " ".join(f"word_{i}" for i in range(1400)) + " END_OF_SOURCE"
    assert "".join(faithful_rewrite.source_chunks(source)) == source
    calls = []
    fail = [True]
    def chat(engine, key, model, prompt, max_tokens):
        data = json.loads(prompt.split("Reference JSON:\n", 1)[1])
        calls.append(data)
        if len(calls) == 2 and fail[0]:
            raise requests.ConnectionError("reset10054")
        return data["chunk"].strip()
    monkeypatch.setattr(rewrite, "chat_text", chat)
    args = (source, "fastgen", "fixture", None, 1400, "английском", str(tmp_path))
    with pytest.raises(requests.ConnectionError):
        rewrite.rewrite(*args, preserve_structure=True, source_profile=PROFILE)
    fail[0] = False
    output = rewrite.rewrite(*args, preserve_structure=True, source_profile=PROFILE)
    assert output.split() == source.split()
    assert len(calls) == 4  # chunk 1 retained, failed chunk 2 retried, then chunk 3
    assert calls[-1]["chunk"].endswith("END_OF_SOURCE")
    assert calls[0]["source_profile"]["narrative_style"] == PROFILE["narrative_style"]
    rewrite.rewrite(*args, preserve_structure=True, source_profile=PROFILE)
    assert len(calls) == 4


def test_full_rewrite_rejects_severely_shortened_part(monkeypatch, tmp_path):
    monkeypatch.setattr(rewrite, "chat_text", lambda *a, **k: "just a summary")
    with pytest.raises(rewrite.RewriteError, match="слишком сократила"):
        rewrite.rewrite("fact " * 200, "fastgen", "fixture", None, 200, "английском", str(tmp_path), preserve_structure=True)
    assert not list(tmp_path.glob("*/part_*.json"))
    assert list(tmp_path.glob("*/source.txt"))


def test_video_profile_affects_generated_style_and_cache(tmp_path):
    pipeline.write_json(str(tmp_path / "source_profile.json"), PROFILE)
    llm = Mock()
    llm.chat_json.return_value = {"style": "flat illustration", "characters": []}
    style = pipeline.build_style(llm, "narration", str(tmp_path), lambda *a: None, "ultrareal")
    assert "Плоская рисованная" in llm.chat_json.call_args.args[0]
    assert style["style_key"].startswith("source_")
    assert pipeline.style_line_of(style).startswith("Reference visual style")
    pipeline.build_style(llm, "narration", str(tmp_path), lambda *a: None, "ultrareal")
    assert llm.chat_json.call_count == 1


def test_youtube_analysis_is_passed_to_ordered_rewrite(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(rewrite, "youtube_transcript", lambda *a, **k: "original full narration")
    monkeypatch.setattr(youtube_source, "video", lambda *a, **k: "fixture.mp4")
    analyser = Mock(return_value=PROFILE)
    monkeypatch.setattr(source_analysis, "analyse", analyser)
    writer = Mock(return_value="new narration")
    monkeypatch.setattr(rewrite, "rewrite", writer)
    profiles = []
    result = pipeline.rewrite_script({"fastgen_api_key": "fixture"},
        {"source": "youtube", "url": "OhnfppMraXY", "engine": "fastgen", "analyze_video": True},
        lambda *a: None, profiles.append)
    assert result == "new narration" and profiles == [PROFILE]
    assert analyser.call_args.args[1] == "original full narration"
    assert writer.call_args.kwargs == {"source_profile": PROFILE, "preserve_structure": True}
    assert writer.call_args.args[4] == 3


def test_analysis_failure_cannot_silently_generate_text_only(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(rewrite, "youtube_transcript", lambda *a, **k: "full narration")
    monkeypatch.setattr(youtube_source, "video", lambda *a, **k: "fixture.mp4")
    monkeypatch.setattr(source_analysis, "analyse", Mock(side_effect=source_analysis.SourceAnalysisError("vision unavailable")))
    writer = Mock()
    monkeypatch.setattr(rewrite, "rewrite", writer)
    with pytest.raises(source_analysis.SourceAnalysisError):
        pipeline.rewrite_script({"fastgen_api_key": "fixture"},
            {"source": "youtube", "url": "OhnfppMraXY", "analyze_video": True}, lambda *a: None)
    writer.assert_not_called()


def test_voice_and_profile_get_independent_downstream_timing_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(pipeline, "make_voiceover", lambda cfg, text, service, voice, directory, progress: str(Path(directory) / "audio.mp3"))
    def resolve(voice, profile=None):
        return pipeline.resolve_source({}, None, "same script", "edge", voice, lambda *a: None, source_profile=profile)[1]
    first = resolve("voice1")
    Path(first, "transcript.json").write_text('{"words": []}', encoding="utf-8")
    assert resolve("voice1") == first
    assert resolve("voice2") != first
    second = resolve("voice1", PROFILE)
    assert second != first
    assert json.loads(Path(second, "source_profile.json").read_text(encoding="utf-8")) == PROFILE
    assert Path(first, "transcript.json").exists()


def test_reviewed_profile_token_rejects_wrong_job(monkeypatch):
    monkeypatch.setattr(app, "STATE", {"running": False, "preparing": False,
                                      "source_profile_id": "current", "source_profile": PROFILE})
    assert app.Api().start({"source_profile_id": "stale"})["ok"] is False


def test_windows_bundle_runtime_found_without_path(monkeypatch, tmp_path):
    import fastgen_api
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    executable = runtime / "deno.exe"
    executable.write_bytes(b"fixture")
    executable.chmod(0o700)
    monkeypatch.setattr(youtube_source.sys, "platform", "win32")
    monkeypatch.setattr(youtube_source.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(fastgen_api, "app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(youtube_source.shutil, "which", lambda _: None)
    assert youtube_source.options()["js_runtimes"]["deno"]["path"] == str(executable.resolve())


def test_windows_cookie_decryption_error_has_specific_action():
    cause = RuntimeError("Failed to decrypt with DPAPI")
    error = RuntimeError("failed to load cookies")
    error.__cause__ = cause
    assert "Firefox" in youtube_source.error_hint(error)
    assert "расшифровку" in youtube_source.error_hint(error)
