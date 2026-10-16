"""Helpers every command module uses: the vault root, the vault lock wrapper and Claude Code's deny list."""
from __future__ import annotations

import argparse
import functools
import json
import re
import sys
from pathlib import Path

from .. import canvas, frontmatter, lock
from ..fsutil import write_text_atomic

_SETTINGS_PATH = ".claude/settings.json"


# Kept in sync with templates/.claude/settings.json -- see _sync_permissions_deny.
_BASE_DENY_RULES = [
    "Write(content/**)",
    "Edit(content/**)",
    "Write(project.canvas)",
    "Edit(project.canvas)",
    "Write(PROJECT.md)",
    "Edit(PROJECT.md)",
    "Write(ARCHITECTURE.md)",
    "Edit(ARCHITECTURE.md)",
    "Write(histos.config.yaml)",
    "Edit(histos.config.yaml)",
    "Write(evals/**)",
    "Edit(evals/**)",
]


def _permission_path(path: Path) -> str:
    """Converts an absolute filesystem path to Claude Code's `//...` absolute-path
    permission-rule syntax (settings.json permissions.deny). Windows paths are normalized
    to POSIX form with a lowercase drive segment, matching how Claude Code itself
    normalizes paths before matching (C:\\Users\\a -> /c/Users/a). Glob-special characters
    that could occur in a real file name ([, ], *, ?, !) are escaped so the rule matches
    this file literally instead of accidentally behaving as a pattern.
    """
    posix = path.resolve().as_posix()
    if len(posix) >= 2 and posix[1] == ":":
        posix = f"/{posix[0].lower()}{posix[2:]}"
    escaped = re.sub(r"([\[\]*?!])", r"\\\1", posix)
    return f"/{escaped}"


def _all_registered_sources(vault_root: Path, data: dict) -> list[Path]:
    paths = []
    for card in canvas.cards(data):
        md_path = canvas.card_file_path(vault_root, card)
        if not md_path.exists():
            continue
        meta, _ = frontmatter.read(md_path)
        for src in meta.get("sources") or []:
            paths.append(Path(src).expanduser())
    return paths


def _sync_permissions_deny(vault_root: Path, source_paths: list[Path]) -> None:
    """Best-effort, Claude-Code-specific hardening (see Trust model in the README): keeps
    .claude/settings.json's permissions.deny covering the base vault paths plus every
    currently registered source file, so the agent can't write to reference material
    either. Add-only -- never removes a rule, so a vault whose settings.json predates this
    (or one where the user added their own rules by hand) never gets silently rewritten
    out from under them. Does nothing if the vault has no .claude/settings.json at all
    (not a Claude Code vault, or the user deleted it on purpose).
    """
    settings_path = vault_root / _SETTINGS_PATH
    if not settings_path.exists():
        return
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    deny = settings.setdefault("permissions", {}).setdefault("deny", [])
    wanted = list(_BASE_DENY_RULES)
    for path in source_paths:
        rule = _permission_path(path)
        wanted += [f"Write({rule})", f"Edit({rule})"]

    changed = False
    for rule in wanted:
        if rule not in deny:
            deny.append(rule)
            changed = True

    if changed:
        try:
            write_text_atomic(settings_path, json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
        except (OSError, canvas.HistosError):
            pass  # best-effort -- must never fail the describe command that triggered this


def _vault_root() -> Path:
    return Path.cwd()


def _with_vault_lock(func):
    """For the agent-side commands that load and save the vault themselves (the human-side
    ones lock inside operations.py, so the desktop app gets the same protection)."""
    @functools.wraps(func)
    def wrapper(args: argparse.Namespace) -> int:
        if getattr(args, "dry_run", False):  # writes nothing, so it waits for nobody
            return func(args)
        target = getattr(args, "id", None) or " ".join(getattr(args, "ids", None) or [])
        try:
            with lock.vault_lock(_vault_root(), f"{args.command} {target}".strip()):
                return func(args)
        except canvas.HistosError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    return wrapper
