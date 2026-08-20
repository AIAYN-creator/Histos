"""Histos desktop app -- a pywebview shell over operations.py.

Stage 2 spike (see the "Histos desktop app" plan): prove that pywebview + PyInstaller
actually produce a working .exe on this machine, and specifically that the schema and
agent templates (loaded via importlib.resources -- see operations.py / canvas.py) resolve
correctly inside a *frozen* build, not just when running from source. The UI here is
deliberately minimal: pick a vault folder, call operations.get_status() on it, show the
result. Stage 3 replaces this with the real review-loop screen.
"""
from __future__ import annotations

import dataclasses
import os
import tempfile
from pathlib import Path

import webview

from .. import canvas, operations


def _web_dir() -> Path:
    return Path(__file__).resolve().parent / "web"


class Api:
    """Exposed to the page as window.pywebview.api.<method>(...). Every method catches
    HistosError itself and returns a single, uniform shape -- {"ok": bool, ...} -- so the
    JS side never has to guess how a Python exception crossed the bridge.
    """

    def pick_vault_folder(self) -> dict:
        window = webview.windows[0]
        selection = window.create_file_dialog(webview.FileDialog.FOLDER)
        if not selection:
            return {"ok": False, "error": "No folder selected"}
        return {"ok": True, "path": selection[0]}

    def get_status(self, vault_path: str) -> dict:
        try:
            result = operations.get_status(Path(vault_path))
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
        "Histos (spike)",
        str(_web_dir() / "index.html"),
        js_api=Api(),
        width=900,
        height=650,
    )
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
