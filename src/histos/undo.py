"""Single-level atomic undo (`histos undo`, `dec-undo-spec`).

Stores a compact pre-state snapshot at `.histos/undo_last.json` before reversible
vault-mutating commands commit, and refuses unconditionally (no `--force`) to revert
human approvals, rejections, or drifted/stale state. `.histos/events.jsonl` remains
strictly append-only: reverting emits an `"undone"` event.
"""
from __future__ import annotations

import base64
import contextvars
import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

from . import canvas, events
from .errors import HistosError
from .fsutil import write_bytes_atomic, write_text_atomic
from .lock import locked

SNAPSHOT_FILENAME = "undo_last.json"

REVERSIBLE_EVENTS = frozenset({
    "card_created",
    "dependency_added",
    "described",
    "assigned",
    "classified",
    "model_proposed",
    "model_approved",
    "model_cleared",
    "proposed",
    "proposal_refused",
    "edit_accepted",
    "implementation_started",
    "implementation_done",
    "guide_proposed",
    "plan_proposed",
    "arranged",
    "archived",
})

HARD_BLOCKED_EVENTS = frozenset({
    "approved",
    "rejected",
    "guide_approved",
    "guide_rejected",
    "plan_approved",
    "plan_rejected",
    "vault_created",
    "config_added",
    "state_recomputed",
    "undone",
    "retry",
})

_DECISION_EVENTS = frozenset({
    "approved",
    "rejected",
    "guide_approved",
    "guide_rejected",
    "plan_approved",
    "plan_rejected",
    "retry",
})

# Events whose execution invalidates and removes `.histos/undo_last.json`.
# Note: `config_added` is deliberately omitted from deletion so `init --add-config`
# does not remove files created before it; `HARD_BLOCKED_EVENTS` + `target_ts`
# mismatch still unconditionally refuse undoing `config_added`.
_CLEAR_SNAPSHOT_EVENTS = _DECISION_EVENTS | frozenset({
    "vault_created",
    "state_recomputed",
    "undone",
})

_PROTECTED_REL_PATHS = frozenset({
    f"{events.LOG_DIR}/{events.LOG_FILENAME}",
    f"{events.LOG_DIR}/{events.VAULT_ID_FILENAME}",
    f"{events.LOG_DIR}/{events.COLORS_FILENAME}",
    f"{events.LOG_DIR}/{SNAPSHOT_FILENAME}",
    ".histos.lock",
})


@dataclass
class _StagedSnapshot:
    vault_root: Path
    command: str
    log_count_before: int
    has_stage: bool = False
    files: dict[str, dict] = field(default_factory=dict)


@dataclass(frozen=True)
class UndoResult:
    reverted_event: str
    reverted_ts: str
    card: str | None = None
    restored_files: list[str] = field(default_factory=list)


_current: contextvars.ContextVar[_StagedSnapshot | None] = contextvars.ContextVar(
    "histos_undo_session", default=None,
)


def snapshot_path(vault_root: Path) -> Path:
    return vault_root / events.LOG_DIR / SNAPSHOT_FILENAME


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canvas_sha256(vault_root: Path) -> str | None:
    path = canvas.vault_canvas_path(vault_root)
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError:
        return None


def _safe_relpath(vault_root: Path, path: Path) -> str | None:
    root = vault_root.resolve()
    try:
        resolved = path.resolve()
        rel = resolved.relative_to(root).as_posix()
    except (OSError, ValueError):
        return None
    if not rel or rel == "." or rel in _PROTECTED_REL_PATHS:
        return None
    return rel


def _capture_entry(path: Path) -> dict:
    if not path.exists():
        return {"existed": False}
    if path.is_symlink() or not path.is_file():
        raise HistosError(f"cannot snapshot non-regular file '{path}'")
    return {
        "existed": True,
        "b64": base64.b64encode(path.read_bytes()).decode("ascii"),
    }


def stage(vault_root: Path, paths: Iterable[Path]) -> None:
    """Captures exact pre-mutation bytes for `paths` (plus critical-path ledger) in the active session."""
    sess = _current.get()
    if sess is None:
        return
    sess.has_stage = True
    candidates = list(paths) + [vault_root / events.LOG_DIR / "critical-path.json"]
    for p in candidates:
        rel = _safe_relpath(vault_root, p)
        if rel is None or rel in sess.files:
            continue
        sess.files[rel] = _capture_entry(p)


def _count_events(vault_root: Path) -> int:
    try:
        return len(events.read(vault_root))
    except (OSError, ValueError):
        return 0


def _clear_snapshot(vault_root: Path) -> None:
    snapshot_path(vault_root).unlink(missing_ok=True)


def _save_snapshot(vault_root: Path, payload: dict) -> None:
    target = snapshot_path(vault_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(target, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


@contextmanager
def session(vault_root: Path, command: str) -> Iterator[None]:
    """Tracks staged pre-state inside `lock.vault_lock` and commits or clears `.histos/undo_last.json`."""
    if _current.get() is not None:
        yield
        return
    sess = _StagedSnapshot(
        vault_root=vault_root,
        command=command,
        log_count_before=_count_events(vault_root),
    )
    token = _current.set(sess)
    try:
        yield
    except BaseException:
        raise
    else:
        _commit_session(sess)
    finally:
        _current.reset(token)


def _commit_session(sess: _StagedSnapshot) -> None:
    try:
        all_events = events.read(sess.vault_root)
    except (OSError, ValueError):
        return
    new_events = all_events[sess.log_count_before:]
    if not new_events:
        return
    last_event = new_events[-1]
    last_name = last_event.get("event")
    if last_name in REVERSIBLE_EVENTS and sess.has_stage:
        payload = {
            "v": 1,
            "event": last_name,
            "target_ts": last_event.get("ts"),
            "card": last_event.get("card"),
            "command": sess.command,
            "post_colors": events._colors(sess.vault_root),
            "post_canvas_sha256": _canvas_sha256(sess.vault_root),
            "files": sess.files,
        }
        _save_snapshot(sess.vault_root, payload)
    elif last_name in _CLEAR_SNAPSHOT_EVENTS or last_name in REVERSIBLE_EVENTS:
        _clear_snapshot(sess.vault_root)


def refresh_canvas_digest(vault_root: Path) -> None:
    """Updates `post_canvas_sha256` when read-only `status` legitimately repaints critical-path arrows."""
    path = snapshot_path(vault_root)
    if not path.exists():
        return
    try:
        snap = json.loads(path.read_text(encoding="utf-8"))
        all_events = events.read(vault_root)
    except (OSError, ValueError):
        return
    if not isinstance(snap, dict) or not all_events:
        return
    latest = all_events[-1]
    if snap.get("event") != latest.get("event") or snap.get("target_ts") != latest.get("ts"):
        return
    if events._has_baseline(vault_root) and events._colors(vault_root) != events._last_known_colors(vault_root):
        return
    snap["post_canvas_sha256"] = _canvas_sha256(vault_root)
    _save_snapshot(vault_root, snap)


def _refusal_message(event_name: str) -> str:
    if event_name in _DECISION_EVENTS:
        return f"cannot undo '{event_name}' -- human decisions cannot be reverted"
    if event_name == "undone":
        return "cannot undo 'undone' -- only one level of undo is supported"
    if event_name == "state_recomputed":
        return "cannot undo 'state_recomputed' -- board state changed or was recomputed"
    return f"cannot undo '{event_name}' -- this event is not reversible"


def _validate_snapshot_files(vault_root: Path, files: object) -> dict[Path, bytes | None]:
    if not isinstance(files, dict):
        raise HistosError("cannot undo: undo snapshot is invalid ('files' must be a mapping)")
    root = vault_root.resolve()
    plan: dict[Path, bytes | None] = {}
    for rel, entry in files.items():
        if not isinstance(rel, str) or not rel or Path(rel).is_absolute() or ".." in Path(rel).parts:
            raise HistosError(f"cannot undo: unsafe path '{rel}' in undo snapshot")
        if rel in _PROTECTED_REL_PATHS:
            raise HistosError(f"cannot undo: protected path '{rel}' cannot be overwritten")
        target = (root / rel).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            raise HistosError(f"cannot undo: path '{rel}' escapes the vault") from None
        if target.is_symlink():
            raise HistosError(f"cannot undo: '{rel}' is a symbolic link")
        if not isinstance(entry, dict) or not isinstance(entry.get("existed"), bool):
            raise HistosError(f"cannot undo: invalid snapshot entry for '{rel}'")
        if entry["existed"]:
            b64 = entry.get("b64")
            if not isinstance(b64, str):
                raise HistosError(f"cannot undo: missing data for '{rel}' in undo snapshot")
            try:
                plan[target] = base64.b64decode(b64.encode("ascii"), validate=True)
            except ValueError as e:
                raise HistosError(f"cannot undo: corrupt data for '{rel}' ({e})") from None
        else:
            plan[target] = None
    return plan


@locked("undo")
def undo_last(vault_root: Path) -> UndoResult:
    """Atomically reverts the last reversible command using `.histos/undo_last.json`.

    Refuses with `HistosError` (and no `--force` escape hatch) if the latest event is a
    human approval/rejection or non-reversible event, if there is no matching snapshot, or
    if canvas state drifted outside Histos. Emits an `"undone"` event (append-only).
    """
    canvas.load(vault_root)  # ensures vault exists and project.canvas is valid JSON

    try:
        all_events = events.read(vault_root)
    except (OSError, ValueError) as e:
        raise HistosError(f"cannot read event log: {e}") from None

    if not all_events:
        raise HistosError("nothing to undo -- no recorded events in this vault")

    latest = all_events[-1]
    latest_name = str(latest.get("event") or "")
    latest_ts = str(latest.get("ts") or "")

    if latest_name in HARD_BLOCKED_EVENTS:
        raise HistosError(_refusal_message(latest_name))
    if latest_name not in REVERSIBLE_EVENTS:
        raise HistosError(f"cannot undo '{latest_name}' -- not a reversible event")

    if events._has_baseline(vault_root):
        if events._colors(vault_root) != events._last_known_colors(vault_root):
            raise HistosError("cannot undo: canvas colors changed outside histos (run 'histos status' to reconcile)")

    snap_file = snapshot_path(vault_root)
    if not snap_file.exists():
        raise HistosError(f"nothing to undo -- no undo snapshot found for '{latest_name}'")
    try:
        snap = json.loads(snap_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise HistosError(f"cannot undo: undo snapshot is unreadable ({e})") from None
    if not isinstance(snap, dict):
        raise HistosError("cannot undo: undo snapshot is invalid")

    if snap.get("event") != latest_name or snap.get("target_ts") != latest_ts:
        raise HistosError(
            f"cannot undo: undo snapshot is stale (snapshot is for '{snap.get('event')}' at "
            f"{snap.get('target_ts')}, latest event is '{latest_name}' at {latest_ts})"
        )

    post_colors = snap.get("post_colors")
    if isinstance(post_colors, dict) and events._colors(vault_root) != post_colors:
        raise HistosError("cannot undo: canvas colors drifted since the last command")

    expected_sha = snap.get("post_canvas_sha256")
    current_sha = _canvas_sha256(vault_root)
    if expected_sha is not None and current_sha != expected_sha:
        raise HistosError("cannot undo: project.canvas changed outside histos since the last command")

    restore_plan = _validate_snapshot_files(vault_root, snap.get("files"))

    restored_rel: list[str] = []
    root = vault_root.resolve()
    for target, raw in restore_plan.items():
        if raw is None:
            if target.exists():
                target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            write_bytes_atomic(target, raw)
        restored_rel.append(target.relative_to(root).as_posix())

    _clear_snapshot(vault_root)

    card = snap.get("card") if isinstance(snap.get("card"), str) else None
    events.emit(
        "undone",
        card=card,
        reverted_event=latest_name,
        reverted_ts=latest_ts,
    )
    return UndoResult(
        reverted_event=latest_name,
        reverted_ts=latest_ts,
        card=card,
        restored_files=sorted(restored_rel),
    )
