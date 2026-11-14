"""A deliberate retry buys at most one new submission, preserving known tasks."""
import copy
from unittest.mock import Mock

import pytest
import requests

import app
import elysium_video as ev
from native_projects import ProjectStore
from tests.test_elysium_video import setup  # shared provider mock and real SQLite


def test_manual_retry_survives_restart_and_buys_only_one_attempt(setup):
    client, session, store, image, root = setup
    session.post.side_effect = requests.Timeout('offline')
    with pytest.raises(ev.ElysiumVideoError) as first:
        client.generate('wave', image, root / 'out.mp4', 4)
    key = first.value.retry_operation
    assert store.operation(key)['status'] == 'uncertain'
    store.authorize_video_retry(key)
    with pytest.raises(ValueError):
        store.authorize_video_retry(key)  # double click cannot grant a second attempt
    client = ev.ElysiumVideo('test-secret', store=ProjectStore(store.root), session=session)
    with pytest.raises(ev.ElysiumVideoError):
        client.generate('wave', image, root / 'out.mp4', 4)
    assert session.post.call_count == 2
    assert len(store.operation(key)['retry_history']) == 1
    with pytest.raises(ev.ElysiumVideoError):
        client.generate('wave', image, root / 'out.mp4', 4)
    assert session.post.call_count == 2  # further restarts do not pay again


@pytest.mark.parametrize('state', [
    {'id': 'task_paid', 'status': 'queued'}, {'id': 'task_paid', 'status': 'failed'},
    {'id': 'task_paid', 'status': 'completed'}, {'status': 'rejected'}, {'status': 'failed'},
    {'status': 'retry_authorized'}, {},
])
def test_manual_retry_never_overwrites_known_or_ineligible_task(tmp_path, state):
    store = ProjectStore(tmp_path)
    key = 'elysium-video:fixture'
    store.operation(key, state)
    with pytest.raises(ValueError):
        store.authorize_video_retry(key)
    assert store.operation(key) == state


def test_bridge_requires_current_failure_and_confirmation_then_resumes_exact_run(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'STATE', {**copy.deepcopy(app.STATE), 'running': False,
                         'preparing': False, 'queue_active': False, 'queue': None, 'log': []})
    key = 'elysium-video:fixture'
    app.STATE['video_retry'] = {'operation': key, 'plan': 83}
    store = ProjectStore(tmp_path)
    store.operation(key, {'status': 'uncertain'})
    monkeypatch.setattr(app, 'data_dir', lambda: str(tmp_path))
    resume = Mock()
    arguments = ({'board': 'original', 'preview': {'seconds': 30}},)
    monkeypatch.setattr(app, '_VIDEO_RESUME', (resume, arguments))
    threads = Mock()
    monkeypatch.setattr(app.threading, 'Thread', threads)
    api = app.Api()
    assert not api.retry_uncertain_video(key)['ok']
    assert not api.retry_uncertain_video('elysium-video:other', True)['ok']
    assert store.operation(key)['status'] == 'uncertain'
    assert api.retry_uncertain_video(key, True)['ok']
    assert store.operation(key)['status'] == 'retry_authorized'
    assert app.STATE['running'] and app.STATE['video_retry'] is None
    assert threads.call_args.kwargs['target'] is resume
    assert threads.call_args.kwargs['args'] == arguments
    assert threads.call_args.kwargs['args'] is not arguments
    assert not api.retry_uncertain_video(key, True)['ok']
    assert threads.call_count == 1
