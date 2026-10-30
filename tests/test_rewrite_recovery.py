"""Recovery must preserve the full source instead of accepting short summaries."""
import json
from unittest.mock import Mock

import pytest
import requests

import netutil
import pipeline
import rewrite


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(netutil, "CANCEL_CHECK", None)
    monkeypatch.setattr(netutil, "pause", lambda _: netutil.check_cancel())
    monkeypatch.setattr(
        requests.sessions.Session, "request",
        Mock(side_effect=AssertionError("Unexpected live HTTP")),
    )


def source_words(count=600):
    return " ".join(f"факт_{index:04d}" for index in range(count))


def reference(prompt):
    return json.loads(prompt.split("Reference JSON:\n", 1)[1])


def full_rewrite(source, directory, **kwargs):
    return rewrite.rewrite(
        source, "gemini", "fixture-key", None, len(source.split()),
        "русском", str(directory), preserve_structure=True, **kwargs,
    )


@pytest.mark.parametrize("rejected", ["", "Очень краткое описание"])
def test_retries_invalid_part_then_caches_only_complete_text(monkeypatch, tmp_path, rejected):
    source = source_words(200)
    prompts = []

    def chat(engine, key, model, prompt, max_tokens):
        prompts.append(prompt)
        return rejected if len(prompts) == 1 else reference(prompt)["chunk"]

    monkeypatch.setattr(rewrite, "chat_text", chat)
    assert full_rewrite(source, tmp_path).split() == source.split()
    assert len(prompts) == 2
    assert prompts[0] != prompts[1]
    assert reference(prompts[0])["chunk"] == reference(prompts[1])["chunk"]
    assert reference(prompts[1])["previous_output"] == ""
    assert full_rewrite(source, tmp_path).split() == source.split()
    assert len(prompts) == 2


def test_persistent_summary_is_bounded_and_never_becomes_a_result(monkeypatch, tmp_path):
    source = source_words()
    chat = Mock(return_value="Только краткое описание")
    monkeypatch.setattr(rewrite, "chat_text", chat)
    with pytest.raises(rewrite.RewriteError, match="сократ|крат|корот"):
        full_rewrite(source, tmp_path)
    # Two tries per node, at most one root and two levels of binary children.
    assert 2 <= chat.call_count <= 14
    assert not list(tmp_path.rglob("result.txt"))
    assert next(tmp_path.rglob("source.txt")).read_text(encoding="utf-8") == source
    for path in tmp_path.rglob("part_*.json"):
        assert not json.loads(path.read_text(encoding="utf-8")).get("text")


def test_split_recovery_keeps_every_source_word_in_order(monkeypatch, tmp_path):
    source = source_words()
    calls = []
    accepted = []

    def chat(engine, key, model, prompt, max_tokens):
        data = reference(prompt)
        calls.append(data)
        if len(data["chunk"].split()) > 150:
            return "ОЧЕНЬ КРАТКО"
        accepted.append(data["chunk"])
        return data["chunk"]

    monkeypatch.setattr(rewrite, "chat_text", chat)
    result = full_rewrite(source, tmp_path)
    assert result.split() == source.split()
    assert "".join(accepted) == source
    assert len(accepted) == 4
    assert all("ОЧЕНЬ КРАТКО" not in data["previous_output"] for data in calls)
    count = len(calls)
    assert full_rewrite(source, tmp_path) == result
    assert len(calls) == count


def test_interrupted_split_resumes_without_repeating_parent_or_saved_child(monkeypatch, tmp_path):
    source = source_words()
    calls = []
    interrupted = [False]

    def chat(engine, key, model, prompt, max_tokens):
        data = reference(prompt)
        calls.append(data)
        if len(data["chunk"].split()) > 300:
            return "Короткое резюме"
        if data["chunk"].split()[0] == "факт_0300" and not interrupted[0]:
            interrupted[0] = True
            raise requests.ConnectionError("interrupted second child")
        return data["chunk"]

    monkeypatch.setattr(rewrite, "chat_text", chat)
    with pytest.raises(requests.ConnectionError, match="second child"):
        full_rewrite(source, tmp_path)
    count = len(calls)
    assert full_rewrite(source, tmp_path).split() == source.split()
    assert len(calls) == count + 1
    assert calls[-1]["chunk"].split()[0] == "факт_0300"
    assert calls[-1]["previous_output"].endswith("факт_0299")


def test_cancellation_after_short_response_stops_before_retry(monkeypatch, tmp_path):
    cancelled = [False]

    def check_cancel():
        if cancelled[0]:
            raise pipeline.Cancelled()

    def chat(*args, **kwargs):
        cancelled[0] = True
        return "Коротко"

    writer = Mock(side_effect=chat)
    monkeypatch.setattr(netutil, "CANCEL_CHECK", check_cancel)
    monkeypatch.setattr(rewrite, "chat_text", writer)
    with pytest.raises(pipeline.Cancelled):
        full_rewrite(source_words(), tmp_path)
    assert writer.call_count == 1
    assert not list(tmp_path.rglob("result.txt"))


@pytest.mark.parametrize("message", ["Gemini 401: unauthorized", "Gemini отклонил запрос: SAFETY"])
def test_terminal_provider_error_is_not_retried_or_split(monkeypatch, tmp_path, message):
    writer = Mock(side_effect=rewrite.RewriteError(message))
    monkeypatch.setattr(rewrite, "chat_text", writer)
    with pytest.raises(rewrite.RewriteError, match=message):
        full_rewrite(source_words(), tmp_path)
    assert writer.call_count == 1
    assert not list(tmp_path.rglob("result.txt"))


def test_truncated_response_gets_a_new_complete_attempt(monkeypatch, tmp_path):
    source = source_words(200)
    calls = []

    def chat(engine, key, model, prompt, max_tokens):
        calls.append((reference(prompt), max_tokens))
        if len(calls) == 1:
            raise rewrite.IncompleteRewrite("MAX_TOKENS")
        return reference(prompt)["chunk"]

    monkeypatch.setattr(rewrite, "chat_text", chat)
    assert full_rewrite(source, tmp_path).split() == source.split()
    assert len(calls) == 2
    assert calls[1][0]["chunk"] == calls[0][0]["chunk"]
    assert calls[1][1] >= calls[0][1]


def test_cached_short_part_is_revalidated_before_reuse(monkeypatch, tmp_path):
    source = source_words(200)
    writer = Mock(side_effect=lambda engine, key, model, prompt, max_tokens: reference(prompt)["chunk"])
    monkeypatch.setattr(rewrite, "chat_text", writer)
    assert full_rewrite(source, tmp_path).split() == source.split()
    part = next(tmp_path.rglob("part_0000.json"))
    cached = json.loads(part.read_text(encoding="utf-8"))
    cached["text"] = "Краткая сводка"
    part.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")
    assert full_rewrite(source, tmp_path).split() == source.split()
    assert writer.call_count == 2


def test_cancel_during_final_truncated_attempt_remains_cancellation(monkeypatch, tmp_path):
    cancelled = [False]
    calls = []

    def check_cancel():
        if cancelled[0]:
            raise pipeline.Cancelled()

    def chat(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            cancelled[0] = True
        raise rewrite.IncompleteRewrite("MAX_TOKENS")

    monkeypatch.setattr(netutil, "CANCEL_CHECK", check_cancel)
    monkeypatch.setattr(rewrite, "chat_text", chat)
    with pytest.raises(pipeline.Cancelled):
        full_rewrite(source_words(100), tmp_path)
    assert len(calls) == 2
    assert not list(tmp_path.rglob("result.txt"))
