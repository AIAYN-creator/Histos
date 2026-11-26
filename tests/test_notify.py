"""Tests for desktop notification dispatch, muting controls, config override, and propose triggers."""
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from conftest import run

from histos import config, context, notify, operations

CONFIG_BASE = """\
tiers:
  - name: reasoning
    heuristics: ["token_count >= 2000"]
  - name: exec
    heuristics: ["code_blocks_present"]
  - name: fast
    heuristics: ["token_count < 200"]
evals:
  enabled: true
  path: evals/
  run_on: propose
"""


def test_is_muted_default_is_false():
    # Fresh isolated HISTOS_HOME from conftest: no notify.json, no env var
    assert notify.is_muted() is False


def test_set_muted_persists_globally():
    assert notify.is_muted() is False

    path = notify.set_muted(True)
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data.get("muted") is True
    assert notify.is_muted() is True

    notify.set_muted(False)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data.get("muted") is False
    assert notify.is_muted() is False


@pytest.mark.parametrize("value", ["1", "true", "True", "yes", "YES", "on", "ON"])
def test_env_var_override_mutes(monkeypatch, value):
    notify.set_muted(False)
    assert notify.is_muted() is False

    monkeypatch.setenv("HISTOS_NO_NOTIFY", value)
    assert notify.is_muted() is True
    # notify() should return False when muted
    assert notify.notify("Histos", "test message") is False


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_env_var_override_unmuted(monkeypatch, value):
    notify.set_muted(False)
    monkeypatch.setenv("HISTOS_NO_NOTIFY", value)
    assert notify.is_muted() is False


def test_config_notifications_enabled_false(tmp_path):
    notify.set_muted(False)
    cfg_text = CONFIG_BASE + "notifications:\n  enabled: false\n"
    (tmp_path / config.CONFIG_FILENAME).write_text(cfg_text, encoding="utf-8")

    assert config.validate(tmp_path) == []
    cfg = config.load(tmp_path)
    assert cfg is not None
    assert cfg.notifications.enabled is False
    assert notify.is_muted(vault_root=tmp_path) is True
    assert notify.notify("Histos", "msg", vault_root=tmp_path) is False


def test_config_notifications_enabled_true(tmp_path):
    notify.set_muted(False)
    cfg_text = CONFIG_BASE + "notifications:\n  enabled: true\n"
    (tmp_path / config.CONFIG_FILENAME).write_text(cfg_text, encoding="utf-8")

    assert config.validate(tmp_path) == []
    cfg = config.load(tmp_path)
    assert cfg is not None
    assert cfg.notifications.enabled is True
    assert notify.is_muted(vault_root=tmp_path) is False


def test_cli_notify_on_off_status(tmp_path, monkeypatch, capsys):
    notify.set_muted(False)

    # 1. Turn off
    ret = run(monkeypatch, tmp_path, "notify", "--off")
    assert ret == 0
    out = capsys.readouterr().out
    assert "off" in out or "muted" in out
    assert notify.is_muted() is True

    # 2. Check status while off
    ret = run(monkeypatch, tmp_path, "notify", "--status")
    assert ret == 0
    out = capsys.readouterr().out
    assert "off" in out

    # 3. Check status with default (no flags)
    ret = run(monkeypatch, tmp_path, "notify")
    assert ret == 0
    out = capsys.readouterr().out
    assert "off" in out

    # 4. Turn on
    ret = run(monkeypatch, tmp_path, "notify", "--on")
    assert ret == 0
    out = capsys.readouterr().out
    assert "on" in out
    assert notify.is_muted() is False

    # 5. Check status while on
    ret = run(monkeypatch, tmp_path, "notify")
    assert ret == 0
    out = capsys.readouterr().out
    assert "on" in out


def test_cli_notify_status_with_env_and_config(tmp_path, monkeypatch, capsys):
    notify.set_muted(False)

    # Env var active
    monkeypatch.setenv("HISTOS_NO_NOTIFY", "1")
    run(monkeypatch, tmp_path, "notify")
    out = capsys.readouterr().out
    assert "HISTOS_NO_NOTIFY" in out

    monkeypatch.delenv("HISTOS_NO_NOTIFY")

    # Config disabled in vault
    (tmp_path / config.CONFIG_FILENAME).write_text(
        CONFIG_BASE + "notifications:\n  enabled: false\n", encoding="utf-8"
    )
    run(monkeypatch, tmp_path, "notify")
    out = capsys.readouterr().out
    assert "histos.config.yaml" in out or "off" in out


def test_propose_triggers_ready_for_review_notification(tmp_path, monkeypatch):
    operations.init_vault(tmp_path)
    run(monkeypatch, tmp_path, "add-card", "cap1", "--title", "First card")
    run(monkeypatch, tmp_path, "assign", "cap1")

    draft = tmp_path / "draft.md"
    draft.write_text("Clean proposed body without placeholders\n", encoding="utf-8")

    mock_notify = MagicMock()
    monkeypatch.setattr(notify, "notify", mock_notify)

    ret = run(monkeypatch, tmp_path, "propose", "cap1", "--file", str(draft))
    assert ret == 0

    mock_notify.assert_called_once()
    args, kwargs = mock_notify.call_args
    assert args[0] == "Histos"
    assert "cap1 ready for review" in args[1]
    assert kwargs.get("vault_root") == tmp_path


def test_failed_eval_triggers_on_third_attempt_only(tmp_path, monkeypatch):
    operations.init_vault(tmp_path)
    run(monkeypatch, tmp_path, "add-card", "cap1", "--title", "First card")
    run(monkeypatch, tmp_path, "assign", "cap1")

    # Draft contains TODO which fails evals/basics.yaml
    draft = tmp_path / "draft.md"
    draft.write_text("TODO: not finished yet\n", encoding="utf-8")

    mock_notify = MagicMock()
    monkeypatch.setattr(notify, "notify", mock_notify)

    # Attempt 1: fail, no notification
    ret1 = run(monkeypatch, tmp_path, "propose", "cap1", "--file", str(draft))
    assert ret1 == 1
    mock_notify.assert_not_called()

    # Attempt 2: fail, no notification
    ret2 = run(monkeypatch, tmp_path, "propose", "cap1", "--file", str(draft))
    assert ret2 == 1
    mock_notify.assert_not_called()

    # Attempt 3: fails 3rd time (MAX_ATTEMPTS reached) -> notify!
    ret3 = run(monkeypatch, tmp_path, "propose", "cap1", "--file", str(draft))
    assert ret3 == 1
    mock_notify.assert_called_once()
    args, kwargs = mock_notify.call_args
    assert args[0] == "Histos"
    assert "cap1 exhausted eval attempts (needs retry)" in args[1]
    assert kwargs.get("vault_root") == tmp_path


def test_backend_subprocess_graceful_error_handling(monkeypatch):
    notify.set_muted(False)
    monkeypatch.setattr(notify, "SUPPRESS_IN_TESTS", False)
    monkeypatch.setenv("DISPLAY", ":0")

    # 1. TimeoutExpired -> returns False, does not raise
    def timeout_runner(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=3.0)

    monkeypatch.setattr(notify, "RUNNER", timeout_runner)
    assert notify.notify("Histos", "testing timeout") is False

    # 2. FileNotFoundError / OSError -> returns False
    def missing_runner(*args, **kwargs):
        raise FileNotFoundError("executable not found")

    monkeypatch.setattr(notify, "RUNNER", missing_runner)
    assert notify.notify("Histos", "testing missing executable") is False

    # 3. Non-zero exit code -> returns False
    def fail_runner(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=1, stdout=b"", stderr=b"failed")

    monkeypatch.setattr(notify, "RUNNER", fail_runner)
    assert notify.notify("Histos", "testing failure code") is False

    # 4. Successful execution -> returns True
    def ok_runner(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(notify, "RUNNER", ok_runner)
    assert notify.notify("Histos", "testing success") is True


def test_linux_headless_suppressed(monkeypatch):
    notify.set_muted(False)
    monkeypatch.setattr(notify, "SUPPRESS_IN_TESTS", False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    called = False

    def check_runner(*args, **kwargs):
        nonlocal called
        called = True
        return subprocess.CompletedProcess(args=args[0], returncode=0)

    monkeypatch.setattr(notify, "RUNNER", check_runner)
    # Headless Linux: no DISPLAY or WAYLAND_DISPLAY -> returns False without calling RUNNER
    assert notify.notify("Histos", "msg") is False
    assert called is False

    # With DISPLAY set -> calls RUNNER
    monkeypatch.setenv("DISPLAY", ":0")
    assert notify.notify("Histos", "msg") is True
    assert called is True


def test_darwin_dispatch(monkeypatch):
    notify.set_muted(False)
    monkeypatch.setattr(notify, "SUPPRESS_IN_TESTS", False)
    monkeypatch.setattr(sys, "platform", "darwin")

    cmd_received = None

    def mac_runner(cmd, *args, **kwargs):
        nonlocal cmd_received
        cmd_received = cmd
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr(notify, "RUNNER", mac_runner)
    assert notify.notify("TestTitle", "TestMessage") is True
    assert cmd_received[0] == "osascript"
    assert "TestTitle" in cmd_received
    assert "TestMessage" in cmd_received


def test_windows_dispatch(monkeypatch):
    notify.set_muted(False)
    monkeypatch.setattr(notify, "SUPPRESS_IN_TESTS", False)
    monkeypatch.setattr(sys, "platform", "win32")

    cmd_received = None
    flags_received = None

    def win_runner(cmd, *args, **kwargs):
        nonlocal cmd_received, flags_received
        cmd_received = cmd
        flags_received = kwargs.get("creationflags")
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr(notify, "RUNNER", win_runner)
    assert notify.notify("WinTitle", "WinMessage") is True
    assert cmd_received[0] == "powershell"
    assert "WinTitle" in cmd_received
    assert "WinMessage" in cmd_received
    assert flags_received == getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
