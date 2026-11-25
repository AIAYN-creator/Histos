"""Desktop notifications: non-blocking, best-effort notifications when proposals need review.

Notifies the human when a card transitions to Proposal pending review or exhausts
its eval attempts (attempt >= MAX_ATTEMPTS).

Muting controls:
- Globally persisted in $HISTOS_HOME/notify.json ({"muted": true|false})
- Environment override: HISTOS_NO_NOTIFY=1 (or true/yes/on)
- Vault configuration override: histos.config.yaml notifications.enabled: false
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from . import config, events, fsutil, lock

FILENAME = "notify.json"
TIMEOUT_SECONDS = 3.0

RUNNER = subprocess.run
SUPPRESS_IN_TESTS = True


def notify_path(home: Path | None = None) -> Path:
    return (home or events.home()) / FILENAME


def is_muted(vault_root: Path | None = None, home: Path | None = None) -> bool:
    """Whether desktop notifications are currently muted.

    Checks:
    1. HISTOS_NO_NOTIFY environment variable (1, true, yes, on).
    2. Global persistent mute state in $HISTOS_HOME/notify.json.
    3. Vault-level config override in histos.config.yaml (notifications.enabled: false).
    """
    env_val = os.environ.get("HISTOS_NO_NOTIFY", "").strip().lower()
    if env_val in ("1", "true", "yes", "on"):
        return True

    try:
        p = notify_path(home)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("muted") is True:
                return True
    except (OSError, ValueError):
        pass

    if vault_root is not None:
        try:
            cfg = config.load(vault_root)
            if cfg is not None and getattr(cfg, "notifications", None) is not None and not cfg.notifications.enabled:
                return True
        except Exception:
            pass

    return False


def set_muted(muted: bool, home: Path | None = None) -> Path:
    """Sets the persistent global mute state in $HISTOS_HOME/notify.json atomically."""
    target = notify_path(home)
    target.parent.mkdir(parents=True, exist_ok=True)
    with lock.file_lock(target.parent / ".notify.lock", "set notify mute"):
        fsutil.write_text_atomic(target, json.dumps({"muted": bool(muted)}, indent=2) + "\n")
    return target


def notify(title: str, message: str, *, vault_root: Path | None = None) -> bool:
    """Dispatches a desktop notification if not muted.

    Returns True only when dispatched (or simulated in test mode), False when muted,
    headless, or on error. Never raises exceptions or blocks the CLI.
    """
    if is_muted(vault_root=vault_root):
        return False
    return _dispatch_os(title, message)


def _dispatch_os(title: str, message: str) -> bool:
    # Auto-suppress real OS subprocess calls during pytest unless tests patch RUNNER or SUPPRESS_IN_TESTS
    if SUPPRESS_IN_TESTS and "PYTEST_CURRENT_TEST" in os.environ and RUNNER is subprocess.run:
        return True

    text_title = str(title) if title else "Histos"
    text_message = str(message) if message else text_title

    try:
        if sys.platform == "win32":
            return _dispatch_windows(text_title, text_message)
        elif sys.platform == "darwin":
            return _dispatch_macos(text_title, text_message)
        elif sys.platform.startswith("linux") or "bsd" in sys.platform:
            return _dispatch_linux(text_title, text_message)
        else:
            return False
    except Exception:
        return False


def _dispatch_windows(title: str, message: str) -> bool:
    ps_script = (
        "& { param($t, $m) try { "
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; "
        "$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
        "$texts = $template.GetElementsByTagName('text'); "
        "$texts.Item(0).AppendChild($template.CreateTextNode($t)) | Out-Null; "
        "$texts.Item(1).AppendChild($template.CreateTextNode($m)) | Out-Null; "
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($template); "
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Histos').Show($toast) "
        "} catch { try { "
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$balloon = New-Object System.Windows.Forms.NotifyIcon; "
        "$balloon.Icon = [System.Drawing.SystemIcons]::Information; "
        "$balloon.BalloonTipTitle = $t; "
        "$balloon.BalloonTipText = $m; "
        "$balloon.Visible = $true; "
        "$balloon.ShowBalloonTip(3000); "
        "Start-Sleep -Milliseconds 500; "
        "$balloon.Dispose() "
        "} catch {} } }"
    )
    cmd = [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-WindowStyle", "Hidden",
        "-Command",
        ps_script,
        title,
        message,
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    res = RUNNER(cmd, timeout=TIMEOUT_SECONDS, creationflags=creationflags, capture_output=True)
    return res.returncode == 0


def _dispatch_macos(title: str, message: str) -> bool:
    cmd = [
        "osascript",
        "-e", "on run argv",
        "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
        "-e", "end run",
        title,
        message,
    ]
    res = RUNNER(cmd, timeout=TIMEOUT_SECONDS, capture_output=True)
    return res.returncode == 0


def _dispatch_linux(title: str, message: str) -> bool:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    cmd = ["notify-send", title, message]
    res = RUNNER(cmd, timeout=TIMEOUT_SECONDS, capture_output=True)
    return res.returncode == 0
