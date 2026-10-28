"""Regression coverage for fork-specific Windows startup/update behavior."""
from unittest.mock import Mock

import pytest

import app
import platform_support


@pytest.fixture
def frozen_windows(monkeypatch):
    monkeypatch.setattr(platform_support.sys, "platform", "win32")
    monkeypatch.setattr(platform_support.sys, "frozen", True, raising=False)
    monkeypatch.setattr(app, "load_cfg", lambda: {"auto_update": True})


def test_old_settings_cannot_check_upstream_feed(frozen_windows, monkeypatch):
    request = Mock(side_effect=AssertionError("Upstream feed must not be contacted"))
    monkeypatch.setattr(app.requests, "get", request)
    assert app.UPDATE_FOLDER_ID == ""
    assert app.check_update() is None
    request.assert_not_called()


def test_ui_cannot_install_legacy_update(frozen_windows, monkeypatch):
    thread = Mock(side_effect=AssertionError("Update thread must not start"))
    monkeypatch.setattr(app.threading, "Thread", thread)
    result = app.Api().install_update("https://example.invalid/upstream.zip", "9.9.9")
    assert result["ok"] is False
    assert "отключено" in result["error"]
    thread.assert_not_called()


def test_stale_download_is_not_applied_on_close(frozen_windows, monkeypatch):
    monkeypatch.setattr(app, "STATE", {"running": False, "preparing": False,
                                     "update": {"phase": "ready", "src": "old-updater"}})
    helper = Mock(side_effect=AssertionError("Old updater must not launch"))
    monkeypatch.setattr(app, "launch_helper", helper)
    assert app.on_closing() is True
    helper.assert_not_called()


@pytest.mark.parametrize("operation,args", [
    ("apply_update", ("unchanged-folder", 0)),
    ("launch_helper", ("unchanged-folder",)),
    ("download_update", ("https://example.invalid/upstream.zip", "9.9.9", "download")),
])
def test_legacy_update_entrypoints_fail_before_mutating_files(frozen_windows, monkeypatch, operation, args):
    remove = Mock(side_effect=AssertionError("No files should be removed"))
    process = Mock(side_effect=AssertionError("No process should be started"))
    monkeypatch.setattr(app.shutil, "rmtree", remove)
    monkeypatch.setattr(app.subprocess, "Popen", process)
    with pytest.raises(RuntimeError, match="отключено"):
        getattr(app, operation)(*args)
    remove.assert_not_called()
    process.assert_not_called()
