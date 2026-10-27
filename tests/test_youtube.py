"""Offline regressions for the reported YouTube + fast-gen failures."""
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
import yt_dlp
import youtube_transcript_api

import app
import netutil
import pipeline
import rewrite
import youtube_source as yt

URL = "https://www.youtube.com/watch?v=OhnfppMraXY"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(netutil, "CANCEL_CHECK", None)
    monkeypatch.setattr(requests.sessions.Session, "request", Mock(side_effect=AssertionError("Unexpected live HTTP")))


@pytest.mark.parametrize("url", [URL, "https://youtu.be/OhnfppMraXY?t=2", "https://youtube.com/shorts/OhnfppMraXY", "OhnfppMraXY"])
def test_link_formats(url):
    assert yt.video_id(url) == "OhnfppMraXY"


def test_foreign_url_rejected():
    with pytest.raises(yt.YouTubeError):
        yt.video_id("https://example.org/watch?v=OhnfppMraXY")


def test_failed_first_track_tries_next_and_accepts_short_captions(monkeypatch):
    first = SimpleNamespace(is_generated=False, fetch=Mock(side_effect=ValueError("broken track")))
    second = SimpleNamespace(is_generated=True, fetch=lambda: [SimpleNamespace(text="Короткий сценарий.")])
    monkeypatch.setattr(youtube_transcript_api, "YouTubeTranscriptApi", lambda **kwargs: SimpleNamespace(list=lambda _: [first, second]))
    assert yt.transcript(URL) == "Короткий сценарий."


def test_yt_dlp_caption_fallback_uses_explicit_browser(monkeypatch):
    monkeypatch.setattr(youtube_transcript_api, "YouTubeTranscriptApi", lambda **kwargs: SimpleNamespace(list=Mock(side_effect=RuntimeError("blocked"))))
    seen = {}
    class Downloader:
        def __init__(self, opts): seen.update(opts)
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, url, download):
            assert download is False
            return {"subtitles": {"ru": [{"ext": "json3", "url": "https://www.youtube.com/api/timedtext"}]}}
        def urlopen(self, url):
            return io.BytesIO(json.dumps({"events": [{"segs": [{"utf8": "Первый текст"}]}, {"segs": [{"utf8": "Второй текст"}]}]}).encode())
    monkeypatch.setattr(yt_dlp, "YoutubeDL", Downloader)
    assert yt.transcript(URL, browser="chrome") == "Первый текст Второй текст"
    assert seen["cookiesfrombrowser"] == ("chrome",)


def test_auth_is_opt_in_and_missing_cookie_file_explained(tmp_path):
    assert "cookiesfrombrowser" not in yt.options()
    assert "cookiefile" not in yt.options()
    with pytest.raises(yt.YouTubeError, match="Файл cookies не найден"):
        yt.options(cookie_file=str(tmp_path / "missing"))


def test_auth_error_has_local_action():
    message = yt.error_hint(RuntimeError("Sign in to confirm you're not a bot. Use --cookies-from-browser"))
    assert "браузер" in message and "cookies.txt" in message


def test_vtt_cleaning():
    assert yt.caption_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<b>Привет</b> &amp; мир\n", "vtt") == "Привет & мир"


def test_audio_partial_is_never_promoted(monkeypatch, tmp_path):
    class Downloader:
        def __init__(self, opts): self.opts = opts
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def download(self, urls):
            Path(self.opts['outtmpl'].replace('%(ext)s', 'm4a.part')).write_bytes(b'incomplete')
    monkeypatch.setattr(yt_dlp, "YoutubeDL", Downloader)
    with pytest.raises(yt.YouTubeError, match="пригодный аудиофайл"):
        yt.audio(URL, str(tmp_path), "/usr/bin")
    assert not (tmp_path / 'yt_OhnfppMraXY.m4a').exists()
    assert list(tmp_path.iterdir()) == []


def test_caption_cancellation_does_not_start_fallback(monkeypatch):
    downloader = Mock()
    monkeypatch.setattr(yt_dlp, "YoutubeDL", downloader)
    monkeypatch.setattr(netutil, "CANCEL_CHECK", Mock(side_effect=pipeline.Cancelled()))
    with pytest.raises(pipeline.Cancelled):
        yt.transcript(URL)
    downloader.assert_not_called()


def test_fastgen_connection_reset_retries_then_recovers(monkeypatch):
    result = Mock(status_code=200)
    result.json.return_value = {"choices": [{"message": {"content": "Сценарий"}}]}
    post = Mock(side_effect=[requests.ConnectionError("ConnectionResetError 10054"), result])
    monkeypatch.setattr(rewrite.requests, "post", post)
    pause = Mock()
    monkeypatch.setattr(netutil, "pause", pause)
    assert rewrite.chat_text('fastgen', 'fixture-key', None, 'prompt', 100) == 'Сценарий'
    assert post.call_count == 2
    pause.assert_called_once()


def test_fastgen_unauthorized_does_not_retry_other_models(monkeypatch):
    post = Mock(return_value=SimpleNamespace(status_code=401, text="unauthorized"))
    monkeypatch.setattr(rewrite.requests, "post", post)
    with pytest.raises(rewrite.RewriteError, match="401"):
        rewrite.chat_text('fastgen', 'fixture-key', None, 'prompt', 100)
    assert post.call_count == 1


def test_failed_model_keeps_youtube_source_for_retry(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "data_dir", lambda: str(tmp_path))
    transcript = Mock(return_value="Исходный текст ролика")
    monkeypatch.setattr(rewrite, "youtube_transcript", transcript)
    model = Mock(side_effect=[rewrite.ModelUnavailable("reset10054"), "Готовый сценарий"])
    monkeypatch.setattr(rewrite, "rewrite", model)
    cfg = {"fastgen_api_key": "fixture-key", "youtube_browser": "chrome"}
    opts = {"source": "youtube", "url": URL, "engine": "fastgen"}
    with pytest.raises(rewrite.ModelUnavailable):
        pipeline.rewrite_script(cfg, opts, lambda *args: None)
    assert pipeline.rewrite_script(cfg, opts, lambda *args: None) == "Готовый сценарий"
    transcript.assert_called_once()
    assert transcript.call_args.kwargs["browser"] == "chrome"
    assert (tmp_path / 'jobs/youtube_sources/OhnfppMraXY.txt').read_text() == "Исходный текст ролика"


def test_pipeline_reports_both_caption_and_audio_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(rewrite, "youtube_transcript", Mock(side_effect=yt.YouTubeError("caption denied")))
    monkeypatch.setattr(rewrite, "youtube_audio", Mock(side_effect=yt.YouTubeError("audio auth")))
    with pytest.raises(rewrite.RewriteError, match=r"caption denied[\s\S]*audio auth"):
        pipeline.rewrite_script({}, {"source": "youtube", "url": URL}, lambda *args: None)


def test_youtube_settings_roundtrip_and_reset(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "CONFIG_PATH", str(tmp_path / 'config.json'))
    api = app.Api()
    api.save_config({"youtube_browser": "chrome"})
    assert app.load_cfg()["youtube_browser"] == "chrome"
    api.save_config({"youtube_cookie_file": "/local/cookies.txt"})
    assert app.load_cfg()["youtube_browser"] == ""
    assert app.load_cfg()["youtube_cookie_file"] == "/local/cookies.txt"
    api.save_config({"youtube_browser": "", "youtube_cookie_file": ""})
    assert not app.load_cfg()["youtube_cookie_file"]
