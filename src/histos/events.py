"""The event log: what happened to each card and when, so later versions can estimate, find the
critical path and report on a project from real history instead of guesses.

    .histos/events.jsonl     one JSON event per line, append-only -- the format is
                             histos-events.schema.json, documented in docs/canvas-schema.md
    .histos/vault.json       the vault's id, so its history is recognizable if it moves
    $HISTOS_HOME/vaults.json every vault that ever recorded an event (default ~/.histos)

Recording rides on the vault lock (lock.vault_lock opens a session): commands `emit()` what
they did, and when the lock is released without an exception the session compares the card
colors before and after, attaches those transitions -- including the ones in *other* cards,
like the dependents an approve unblocks -- and appends everything in one write. A failure to
record never fails the command: the vault change already happened, and that's what matters.
"""
from __future__ import annotations

import contextvars
import json
import os
import re
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from . import canvas, frontmatter

FORMAT_VERSION = 1
LOG_DIR = ".histos"
LOG_FILENAME = "events.jsonl"
VAULT_ID_FILENAME = "vault.json"
REGISTRY_FILENAME = "vaults.json"
HOME_ENV = "HISTOS_HOME"

ACTORS = ("agent", "human")
EVENTS = frozenset({
    "vault_created", "config_added",
    "card_created", "dependency_added", "described", "assigned",
    "classified", "model_proposed", "model_approved", "model_cleared",
    "proposed", "proposal_refused", "approved", "rejected", "retry", "edit_accepted",
    "guide_proposed", "guide_approved", "guide_rejected",
    "plan_proposed", "plan_approved", "plan_rejected",
    "arranged", "state_recomputed",
})

_STATE_NAMES = {
    canvas.BLOCKED: "Blocked",
    canvas.IN_PROGRESS: "In progress",
    canvas.PENDING_REVIEW: "Proposal pending review",
    canvas.APPROVED: "Approved",
    canvas.DEPENDENCY_CHANGE_REQUEST: "Dependency change request",
    canvas.BACKLOG: "Backlog",
}


@dataclass
class _Session:
    vault_root: Path
    actor: str
    before: dict[str, str]
    events: list[dict] = field(default_factory=list)


_current: contextvars.ContextVar[_Session | None] = contextvars.ContextVar("histos_events_session", default=None)


def log_path(vault_root: Path) -> Path:
    return vault_root / LOG_DIR / LOG_FILENAME


def home() -> Path:
    return Path(os.environ.get(HOME_ENV) or Path.home() / ".histos")


def size_of(text: str) -> dict:
    """The size fields of proposed/approved: words counted like the word_count eval does."""
    return {"words": len(re.findall(r"\w+", text)), "lines": len(text.splitlines())}


@contextmanager
def recording(vault_root: Path, actor: str) -> Iterator[None]:
    """One session per command. A nested one joins the outer session."""
    if _current.get() is not None:
        yield
        return
    if actor not in ACTORS:
        raise ValueError(f"unknown actor {actor!r}")
    session = _Session(vault_root=vault_root, actor=actor, before=_colors(vault_root))
    token = _current.set(session)
    try:
        yield
    except BaseException:
        raise  # nothing was (fully) done: nothing to record
    else:
        _flush(session)
    finally:
        _current.reset(token)


def emit(event: str, card: str | None = None, **data) -> None:
    """Adds an event to the current session; written when the lock is released."""
    session = _current.get()
    if session is None:
        raise RuntimeError(f"events.emit({event!r}) outside a vault lock -- nothing would record it")
    if event not in EVENTS:
        raise ValueError(f"unknown event {event!r}")
    record = {
        "v": FORMAT_VERSION,
        "ts": _now(),
        "event": event,
        "card": card,
        "actor": session.actor,
        "worker": _worker(session.vault_root, card),
    }
    if data:
        record["data"] = data
    session.events.append(record)


def read(vault_root: Path) -> list[dict]:
    """Every event, oldest first. A cut-off last line (a crash mid-write) is skipped."""
    events, _ = _parse(vault_root)
    return events


def check_log(vault_root: Path) -> tuple[list[str], list[str]]:
    """(errors, warnings) for `histos validate`: each line against histos-events.schema.json."""
    path = log_path(vault_root)
    if not path.exists():
        return [], []
    from jsonschema import Draft202012Validator

    from .validation import _load_named_schema

    validator = Draft202012Validator(_load_named_schema("histos-events.schema.json"))
    errors, warnings = [], []
    lines = path.read_text(encoding="utf-8").splitlines()
    for n, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            if n == len(lines):
                warnings.append(f"events: line {n} is cut off (a crash while writing?) -- it's ignored")
            else:
                errors.append(f"events: line {n} is not valid JSON")
            continue
        for e in validator.iter_errors(record):
            where = "/".join(str(p) for p in e.path) or "(root)"
            errors.append(f"events: line {n}: {where}: {e.message}")
    return errors, warnings


def _parse(vault_root: Path) -> tuple[list[dict], bool]:
    path = log_path(vault_root)
    if not path.exists():
        return [], False
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    events, cut = [], False
    for n, line in enumerate(lines, start=1):
        try:
            events.append(json.loads(line))
        except ValueError:
            if n == len(lines):
                cut = True
                continue
            raise
    return events, cut


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _colors(vault_root: Path) -> dict[str, str]:
    try:
        data = canvas.load(vault_root)
    except Exception:  # no canvas yet (init), or a broken one the command itself will report
        return {}
    return {c["id"]: c.get("color") for c in canvas.cards(data) if "id" in c}


def _worker(vault_root: Path, card_id: str | None) -> str | None:
    if card_id is None:
        return None
    try:
        meta, _ = frontmatter.read(vault_root / "content" / f"{card_id}.md")
    except Exception:
        return None
    worker = meta.get("worker")
    return worker if isinstance(worker, str) and worker else None


def _transitions(before: dict[str, str], after: dict[str, str]) -> list[dict]:
    changes = []
    for card_id in list(before) + [c for c in after if c not in before]:
        old, new = before.get(card_id), after.get(card_id)
        if old != new:
            changes.append({"card": card_id, "from": _STATE_NAMES.get(old), "to": _STATE_NAMES.get(new)})
    return changes


def _flush(session: _Session) -> None:
    try:
        transitions = _transitions(session.before, _colors(session.vault_root))
        if not session.events and not transitions:
            return
        if not session.events:
            emit("state_recomputed")
        if transitions:
            session.events[-1]["transitions"] = transitions
        _append(session.vault_root, session.events)
    except Exception as e:  # never fail a command that already changed the vault
        print(f"warning: couldn't record the event ({e})", file=sys.stderr)


def _append(vault_root: Path, records: list[dict]) -> None:
    folder = vault_root / LOG_DIR
    folder.mkdir(exist_ok=True)
    vault_id = _ensure_vault_id(vault_root)
    text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    with open(log_path(vault_root), "a", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    try:
        _register_vault(vault_root, vault_id)
    except Exception as e:
        print(f"warning: couldn't add this vault to {home() / REGISTRY_FILENAME} ({e})", file=sys.stderr)


def _ensure_vault_id(vault_root: Path) -> str:
    from .fsutil import write_text_atomic

    path = vault_root / LOG_DIR / VAULT_ID_FILENAME
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(info, dict) and isinstance(info.get("id"), str):
            return info["id"]
    except (OSError, ValueError):
        pass
    info = {"id": str(uuid.uuid4()), "created": _now()}
    write_text_atomic(path, json.dumps(info, indent=2) + "\n")
    return info["id"]


def _read_registry(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    vaults = data.get("vaults") if isinstance(data, dict) else None
    return [v for v in vaults if isinstance(v, dict)] if isinstance(vaults, list) else []


def _register_vault(vault_root: Path, vault_id: str) -> None:
    """Keeps $HISTOS_HOME/vaults.json listing this vault -- only written when something changed."""
    from . import lock
    from .fsutil import write_text_atomic

    entry = {"id": vault_id, "path": str(vault_root.resolve())}
    path = home() / REGISTRY_FILENAME
    if entry in _read_registry(path):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with lock.file_lock(path.parent / ".vaults.lock", "register vault"):
        vaults = [v for v in _read_registry(path) if v.get("id") != vault_id and v.get("path") != entry["path"]]
        vaults.append(entry)
        write_text_atomic(path, json.dumps({"vaults": vaults}, indent=2, ensure_ascii=False) + "\n")
