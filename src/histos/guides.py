"""PROJECT.md and ARCHITECTURE.md change only with the human's approval -- the same propose /
diff / approve / reject cycle as a card, never a temporary write permission: the agent never
writes these files, `histos guide approve` (run by the human) does.

    proposals/guides/PROJECT.md                     the pending change
    approved/guides/PROJECT-20261016-174502.md      the version it replaced, kept on approve
    proposals/guides/.feedback.json                 feedback from the last rejection, per file

Card ids can't contain '/', so none of this ever collides with a card's own files.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import operations
from .errors import HistosError
from .fsutil import write_text_atomic
from .lock import locked

GUIDE_FILES = ("PROJECT.md", "ARCHITECTURE.md")
_SUBDIR = "guides"
_FEEDBACK_FILE = ".feedback.json"


def canonical_name(name: str) -> str:
    """Accepts any capitalization, returns the file's real name; anything else is an error --
    this is not a way to write arbitrary files in the vault."""
    for real in GUIDE_FILES:
        if name.strip().lower() == real.lower():
            return real
    raise HistosError(f"'{name}' is not a guide file -- only {' and '.join(GUIDE_FILES)}")


def proposal_path(vault_root: Path, name: str) -> Path:
    return operations._proposal_path(vault_root, "x").parent / _SUBDIR / name


def _approved_dir(vault_root: Path) -> Path:
    return operations._approved_path(vault_root, "x").parent / _SUBDIR


def _feedback_path(vault_root: Path) -> Path:
    return proposal_path(vault_root, _FEEDBACK_FILE)


def pending(vault_root: Path) -> list[str]:
    return [name for name in GUIDE_FILES if proposal_path(vault_root, name).exists()]


def feedback(vault_root: Path) -> dict[str, str]:
    try:
        data = json.loads(_feedback_path(vault_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if k in GUIDE_FILES and isinstance(v, str)} if isinstance(data, dict) else {}


def _set_feedback(vault_root: Path, name: str, text: str | None) -> None:
    current = feedback(vault_root)
    if text:
        current[name] = text
    elif name in current:
        del current[name]
    else:
        return
    path = _feedback_path(vault_root)
    if current:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(path, json.dumps(current, indent=2, ensure_ascii=False) + "\n")
    else:
        path.unlink(missing_ok=True)


@dataclass
class ProposeResult:
    name: str
    replaced_pending: bool


@locked("guide propose")
def propose(vault_root: Path, name: str, draft_text: str) -> ProposeResult:
    name = canonical_name(name)
    path = proposal_path(vault_root, name)
    replaced = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, draft_text)
    _set_feedback(vault_root, name, None)  # a new proposal answers the old feedback
    return ProposeResult(name=name, replaced_pending=replaced)


@dataclass
class GuideDiff:
    name: str
    current: str
    proposed: str


def get_diff(vault_root: Path, name: str) -> GuideDiff:
    name = canonical_name(name)
    path = proposal_path(vault_root, name)
    if not path.exists():
        raise HistosError(f"no pending change for {name}")
    target = vault_root / name
    current = target.read_text(encoding="utf-8") if target.exists() else ""
    return GuideDiff(name=name, current=current, proposed=path.read_text(encoding="utf-8"))


@dataclass
class ApproveResult:
    name: str
    archived_previous: Path | None


@locked("guide approve")
def approve(vault_root: Path, name: str) -> ApproveResult:
    name = canonical_name(name)
    path = proposal_path(vault_root, name)
    if not path.exists():
        raise HistosError(f"no pending change for {name}")
    target = vault_root / name
    archived = None
    if target.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        archived = _approved_dir(vault_root) / f"{Path(name).stem}-{stamp}.md"
        archived.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(archived, target.read_text(encoding="utf-8"))
    write_text_atomic(target, path.read_text(encoding="utf-8"))
    path.unlink()
    _set_feedback(vault_root, name, None)
    return ApproveResult(name=name, archived_previous=archived)


@locked("guide reject")
def reject(vault_root: Path, name: str, feedback_text: str | None = None) -> str:
    name = canonical_name(name)
    path = proposal_path(vault_root, name)
    if not path.exists():
        raise HistosError(f"no pending change for {name}")
    path.unlink()
    _set_feedback(vault_root, name, feedback_text)
    return name
