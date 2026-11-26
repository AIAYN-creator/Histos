"""Histos desktop app -- a pywebview shell over operations.py.

Stage 5 (see the "Histos desktop app" plan), the last piece: detect when a picked folder
isn't a Histos project yet (is_vault) and offer to start one (init_vault) instead of just
surfacing "can't find project.canvas -- run 'histos init' first", which is exactly the
terminal-shaped failure this whole app exists to avoid. The actual project interview
stays the agent's job (AGENTS.md) -- this wizard is deliberately thin, a no-terminal
equivalent of `histos init` and nothing more. Every Api method catches HistosError itself
and returns a single, uniform shape -- {"ok": bool, ...} -- so the JS side never has to
guess how a Python exception crossed the bridge.
"""
from __future__ import annotations

import dataclasses
import json
import os
import tempfile
import urllib.parse
from pathlib import Path
from typing import Optional

import webview

from .. import canvas, config, evals, guides, operations, pauses, plans

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

    def is_vault(self, vault_path: str) -> dict:
        """Cheap, structural check (does project.canvas exist) instead of trying
        get_status and pattern-matching its error string -- the same discipline as the
        rest of this Api: never make the JS side parse prose meant for a human.
        """
        exists = canvas.vault_canvas_path(Path(vault_path)).exists()
        return {"ok": True, "is_vault": exists}

    def init_vault(self, vault_path: str) -> dict:
        try:
            result = operations.init_vault(Path(vault_path))
        except canvas.HistosError as e:
            return {"ok": False, "error": str(e)}
        return {
            "ok": True,
            "data": {"vault_root": str(result.vault_root), "template_warnings": result.template_warnings},
        }

    def get_status(self, vault_path: str) -> dict:
        try:
            result = operations.get_status(Path(vault_path))
        except canvas.HistosError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "data": dataclasses.asdict(result)}

    def open_in_obsidian(self, vault_path: str) -> dict:
        """Best-effort, not guaranteed: Obsidian's own `open?path=` URI handling has
        several open, longstanding reports of not working reliably on Windows (vault not
        found, silently no-ops when Obsidian is already running). There's no more robust
        alternative available from here -- open_folder() is the reliable fallback the UI
        always shows alongside this button, precisely because this one might not do
        anything visible.
        """
        encoded = urllib.parse.quote(vault_path, safe="")
        try:
            os.startfile(f"obsidian://open?path={encoded}")
        except OSError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True}

    def open_folder(self, vault_path: str) -> dict:
        # os.startfile runs whatever it's given: only ever hand it a folder, never a file.
        if not Path(vault_path).is_dir():
            return {"ok": False, "error": f"not a folder: {vault_path}"}
        try:
            os.startfile(vault_path)
        except OSError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True}

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

    def pause(self, vault_path: Optional[str] = None) -> dict:
        try:
            _, new = pauses.pause()
        except canvas.HistosError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "data": {"paused": True, "new": new}}

    def approve_model(self, vault_path: str, card_id: str, model_name: Optional[str] = None) -> dict:
        try:
            result = operations.approve_model(Path(vault_path), card_id, model_name)
        except canvas.HistosError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "data": dataclasses.asdict(result)}

    def approve_models_batch(self, vault_path: str, items: list) -> dict:
        try:
            results = operations.approve_models_batch(Path(vault_path), items)
        except canvas.HistosError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "data": {"approved": [dataclasses.asdict(r) for r in results]}}

    def retry(self, vault_path: str, card_id: str) -> dict:
        try:
            operations.retry(Path(vault_path), card_id)
        except canvas.HistosError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "data": {"card_id": card_id}}

    def accept_edit(self, vault_path: str, card_ids: list[str] | str) -> dict:
        ids = [card_ids] if isinstance(card_ids, str) else list(card_ids)
        if not ids:
            return {"ok": False, "error": "no cards specified"}
        try:
            accepted = operations.accept_edit(Path(vault_path), ids)
        except canvas.HistosError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "data": {"card_ids": accepted}}

    def get_pending_guides(self, vault_path: str) -> dict:
        try:
            root = Path(vault_path)
            if not root.is_dir():
                raise canvas.HistosError(f"not a folder: {vault_path}")
            items = []
            for name in guides.pending(root):
                diff = guides.get_diff(root, name)
                items.append({
                    "name": diff.name,
                    "current": diff.current,
                    "proposed": diff.proposed,
                    "eval_results": operations._json_safe(guides.stored_evals(root, name)),
                })
            feedback = guides.feedback(root)
        except canvas.HistosError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "data": {"guides": items, "feedback": feedback}}

    def approve_guide(self, vault_path: str, name: str) -> dict:
        try:
            result = guides.approve(Path(vault_path), name)
        except canvas.HistosError as e:
            return {"ok": False, "error": str(e)}
        return {
            "ok": True,
            "data": {
                "name": result.name,
                "archived_previous": str(result.archived_previous) if result.archived_previous else None,
            },
        }

    def reject_guide(self, vault_path: str, name: str, feedback: Optional[str] = None) -> dict:
        try:
            rejected = guides.reject(Path(vault_path), name, feedback or None)
        except canvas.HistosError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "data": {"name": rejected}}

    def get_pending_plan(self, vault_path: str) -> dict:
        try:
            root = Path(vault_path)
            if not root.is_dir():
                raise canvas.HistosError(f"not a folder: {vault_path}")
            plan = plans.pending(root)
            feedback = plans.last_feedback(root)
            if plan is None:
                return {"ok": True, "data": {"plan": None, "feedback": feedback}}
            data = operations._load_valid(root)
            payload = {
                "summary": plan.summary,
                "cards": [dataclasses.asdict(c) for c in plan.cards],
                "levels": plans.levels(plan, data),
                "warnings": plans.warnings(plan, data),
                "errors": plans.structural_errors(plan, data),
                "raw_yaml": plans.plan_path(root).read_text(encoding="utf-8"),
            }
        except canvas.HistosError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "data": {"plan": payload, "feedback": feedback}}

    def approve_plan(self, vault_path: str) -> dict:
        try:
            result = plans.approve(Path(vault_path))
        except canvas.HistosError as e:
            return {"ok": False, "error": str(e)}
        return {
            "ok": True,
            "data": {
                "created": [c["id"] for c in result.created],
                "archived": str(result.archived),
            },
        }

    def reject_plan(self, vault_path: str, feedback: Optional[str] = None) -> dict:
        try:
            cards = plans.reject(Path(vault_path), feedback or None)
        except canvas.HistosError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "data": {"cards": cards}}


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
            operations.init_vault(vault_root)  # takes the vault lock too
            status_result = operations.get_status(vault_root)
            # v3's bundled files: the config template and its schema, the evals templates
            cfg = config.load(vault_root)
            loaded_evals = evals.load(vault_root, cfg)
        except Exception as e:
            print(f"SELFTEST FAIL: {e!r}")
            return 1
        ok = (
            vault_root.joinpath("AGENTS.md").exists()
            and len(status_result.groups) == len(operations.STATE_NAMES)
            and cfg is not None
            and len(loaded_evals) == 5  # basics (2) + guides (3)
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
