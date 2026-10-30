"""Provider response boundaries: only complete narration may enter rewrite caches."""
import json
from unittest.mock import Mock

import pytest
import requests

import netutil
import rewrite


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(netutil, "CANCEL_CHECK", None)
    monkeypatch.setattr(requests.sessions.Session, "request", Mock(side_effect=AssertionError("Unexpected HTTP")))


def response(payload, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload).encode()
    result._content_consumed = True
    return result


def completion(reason, text="complete narration", **message):
    return {"choices": [{"message": {"content": text, **message}, "finish_reason": reason}]}


def gemini(reason, text="complete narration"):
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": reason}]}


@pytest.mark.parametrize("engine,payload", [
    ("openai", completion("length", "partial narration")),
    ("fastgen", completion("length", "partial narration")),
    ("anthropic", {"content": [{"type": "text", "text": "partial narration"}], "stop_reason": "max_tokens"}),
    ("gemini", gemini("MAX_TOKENS", "partial narration")),
    ("gemini", gemini("MAX_TOKENS", "")),
])
def test_truncated_response_is_typed_without_transport_retry(monkeypatch, engine, payload):
    post = Mock(return_value=response(payload))
    pause = Mock()
    monkeypatch.setattr(rewrite.requests, "post", post)
    monkeypatch.setattr(netutil, "pause", pause)
    with pytest.raises(rewrite.IncompleteRewrite):
        rewrite.chat_text(engine, "fixture", None, "rewrite", 2500)
    post.assert_called_once()
    pause.assert_not_called()


@pytest.mark.parametrize("engine,payload", [
    ("openai", completion("content_filter", "partial narration")),
    ("openai", completion("stop", "", refusal="Request declined")),
    ("openai", completion("length", "", refusal="Request declined")),
    ("fastgen", completion("content_filter", "partial narration")),
    ("anthropic", {"content": [{"type": "text", "text": "Request declined"}], "stop_reason": "refusal"}),
    ("gemini", gemini("SAFETY", "partial narration")),
    ("gemini", gemini("RECITATION", "partial narration")),
    ("gemini", {"promptFeedback": {"blockReason": "SAFETY"}}),
])
def test_blocked_response_is_terminal_not_truncation(monkeypatch, engine, payload):
    post = Mock(return_value=response(payload))
    pause = Mock()
    monkeypatch.setattr(rewrite.requests, "post", post)
    monkeypatch.setattr(netutil, "pause", pause)
    with pytest.raises(rewrite.RewriteError) as caught:
        rewrite.chat_text(engine, "fixture", None, "rewrite", 2500)
    assert type(caught.value) is rewrite.RewriteError
    post.assert_called_once()
    pause.assert_not_called()


@pytest.mark.parametrize("engine,payload", [
    ("openai", completion("stop")),
    ("fastgen", completion("stop")),
    ("anthropic", {"content": [{"type": "text", "text": "complete narration"}], "stop_reason": "end_turn"}),
    ("gemini", gemini("STOP")),
])
def test_complete_response_is_returned(monkeypatch, engine, payload):
    monkeypatch.setattr(rewrite.requests, "post", Mock(return_value=response(payload)))
    assert rewrite.chat_text(engine, "fixture", None, "rewrite", 2500) == "complete narration"


def test_gemini_thought_parts_are_not_narration(monkeypatch):
    payload = {"candidates": [{"finishReason": "STOP", "content": {"parts": [
        {"thought": True, "text": "analysis that must not count toward narration length"},
        {"text": "Первое предложение. "},
        {"thought": True, "text": "more analysis"},
        {"thought": False, "text": "Последнее предложение."},
    ]}}]}
    monkeypatch.setattr(rewrite.requests, "post", Mock(return_value=response(payload)))
    assert rewrite.chat_text("gemini", "fixture", None, "rewrite", 2500) == "Первое предложение. Последнее предложение."


def test_gemini_thought_only_response_retries_as_empty(monkeypatch):
    thought_only = {"candidates": [{"finishReason": "STOP", "content": {"parts": [
        {"thought": True, "text": "analysis with no narration"},
    ]}}]}
    post = Mock(side_effect=[response(thought_only), response(gemini("STOP"))])
    pause = Mock()
    monkeypatch.setattr(rewrite.requests, "post", post)
    monkeypatch.setattr(netutil, "pause", pause)
    assert rewrite.chat_text("gemini", "fixture", None, "rewrite", 2500) == "complete narration"
    assert post.call_count == 2
    pause.assert_called_once()


@pytest.mark.parametrize("engine", ["openai", "fastgen", "anthropic", "gemini"])
def test_unauthorized_response_is_terminal(monkeypatch, engine):
    post = Mock(return_value=response({"error": "unauthorized"}, status=401))
    pause = Mock()
    monkeypatch.setattr(rewrite.requests, "post", post)
    monkeypatch.setattr(netutil, "pause", pause)
    with pytest.raises(rewrite.RewriteError, match="401") as caught:
        rewrite.chat_text(engine, "fixture", None, "rewrite", 2500)
    assert type(caught.value) is rewrite.RewriteError
    post.assert_called_once()
    pause.assert_not_called()
