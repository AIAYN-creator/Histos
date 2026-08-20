"""Histos desktop app -- a pywebview shell over operations.py.

Stage 3 (see the "Histos desktop app" plan): the actual review-loop screen -- the
biggest remaining terminal dependency in the human side of the workflow. A user picks a
vault folder once (remembered afterward), sees the cards waiting for review, and can
approve or reject each one with a real before/after comparison instead of a unified diff
dump. Every Api method catches HistosError itself and returns a single, uniform shape --
{"ok": bool, ...} -- so the JS side never has to guess how a Python exception crossed the
bridge.
"""
from __future__ import annotations

import dataclasses
import json
import os
import tempfile
from pathlib import Path
from typing import Optional

import webview

from .. import canvas, operations

_CONFIG_PATH = Path.home() / ".histos" / "gui_config.json"


def _web_dir() -> Path:
    return Path(__file__).resolve().parent / "web"


def _load_config() -> dict:
    if not _CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_config(config: dict) -> None:
    try:
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
    except OSError:
        pass  # remembering the last vault is a convenience, never worth failing an action over


class Api:
    def get_last_vault(self) -> dict:
        """Used on startup to skip the folder picker if we already know where the user's
        vault is. Re-checks the folder still exists -- a saved path can go stale (moved,
        deleted, on an unplugged drive) and there's no point handing back a dead path.
        """
        path = _load_config().get("last_vault")
        if path and Path(path).is_dir():
            return {"ok": True, "path": path}
        return {"ok": True, "path": None}

    def pick_vault_folder(self) -> dict:
        window = webview.windows[0]
        selection = window.create_file_dialog(webview.FileDialog.FOLDER)
        if not selection:
            return {"ok": False, "error": "No folder selected"}
        path = selection[0]
        config = _load_config()
        config["last_vault"] = path
        _save_config(config)
        return {"ok": True, "path": path}

    def get_pending_reviews(self, vault_path: str) -> dict:
        try:
            result = operations.get_status(Path(vault_path))
        except canvas.HistosError as e:
            return {"ok": False, "error": str(e)}
        pending = next(g for g in result.groups if g.color == canvas.PROPUESTA_PENDIENTE)
        return {"ok": True, "data": dataclasses.asdict(pending)}

    def get_diff(self, vault_path: str, card_id: str) -> dict:
        try:
            result = operations.get_diff(Path(vault_path), card_id)
        except canvas.HistosError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "data": dataclasses.asdict(result)}

    def approve(self, vault_path: str, card_id: str) -> dict:
        try:
            result = operations.approve(Path(vault_path), card_id)
        except canvas.HistosError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "data": dataclasses.asdict(result)}

    def reject(self, vault_path: str, card_id: str, feedback: Optional[str] = None) -> dict:
        try:
            result = operations.reject(Path(vault_path), card_id, feedback=feedback or None)
        except canvas.HistosError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "data": dataclasses.asdict(result)}


def _selftest() -> int:
    """Headless check for the packaging spike, run instead of opening a window when
    HISTOS_GUI_SELFTEST is set. Proves operations.py's resource loading (the JSON schema,
    the agent templates -- both read via importlib.resources) actually resolves inside
    *this* build, frozen or not, without needing anyone to click through a real window --
    that's the one thing a PyInstaller build can silently get wrong that source runs can't.
    """
    with tempfile.TemporaryDirectory() as tmp:
        vault_root = Path(tmp)
        try:
            operations.init_vault(vault_root)
            status_result = operations.get_status(vault_root)
        except Exception as e:
            print(f"SELFTEST FAIL: {e!r}")
            return 1
        ok = (
            vault_root.joinpath("AGENTS.md").exists()
            and len(status_result.groups) == len(operations.STATE_NAMES)
        )
        print("SELFTEST PASS" if ok else "SELFTEST FAIL: unexpected result shape")
        return 0 if ok else 1


def main() -> int:
    if os.environ.get("HISTOS_GUI_SELFTEST"):
        return _selftest()

    webview.create_window(
        "Histos",
        str(_web_dir() / "index.html"),
        js_api=Api(),
        width=1000,
        height=700,
        min_size=(700, 500),
    )
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
