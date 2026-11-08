"""The human's pause: "I'm away" -- asleep, on something else, gone until tomorrow.

While paused, the review clock stops in every vault: a card waiting for the human overnight
didn't cost the human eight hours of reviewing. The agent's clock never stops -- it can keep
working an AFK queue. It's one pause per person, not per vault, kept next to the vault registry:

    $HISTOS_HOME/pauses.jsonl     {"event": "pause", "ts": ...} / {"event": "resume", "ts": ..., "by": "approve"}

There's no resume command: the human's next decision -- approve, reject, retry, accept-edit, a
guide's or a plan's approve/reject, arrange; from the terminal or the desktop app -- ends the
pause on its own. Commands the agent runs too (status, context...) never do, or an agent working
overnight would end a pause the human is still in.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import events

FILENAME = "pauses.jsonl"
# The lock.locked commands that are a human decision -- the human is back.
DECISIONS = frozenset({
    "approve", "reject", "retry", "accept-edit", "arrange",
    "guide approve", "guide reject", "plan approve", "plan reject",
})


@dataclass(frozen=True)
class Pause:
    start: datetime
    end: datetime | None  # None: still paused


def path(home: Path | None = None) -> Path:
    return (home or events.home()) / FILENAME


def read(home: Path | None = None) -> list[Pause]:
    """Every pause, oldest first. A cut-off last line is ignored, like the event log."""
    try:
        lines = path(home).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    pauses: list[Pause] = []
    for line in lines:
        try:
            record = json.loads(line)
            ts = datetime.fromisoformat(record["ts"].replace("Z", "+00:00"))
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        if record.get("event") == "pause" and (not pauses or pauses[-1].end is not None):
            pauses.append(Pause(start=ts, end=None))
        elif record.get("event") == "resume" and pauses and pauses[-1].end is None:
            pauses[-1] = Pause(start=pauses[-1].start, end=ts)
    return pauses


def current(home: Path | None = None) -> Pause | None:
    pauses = read(home)
    return pauses[-1] if pauses and pauses[-1].end is None else None


def pause(home: Path | None = None) -> tuple[Pause, bool]:
    """Starts a pause. (the pause, whether it's new) -- pausing twice keeps the first one."""
    existing = current(home)
    if existing:
        return existing, False
    now = _now()
    _append(home, {"event": "pause", "ts": _iso(now)})
    return Pause(start=now, end=None), True


def resume_on(command: str, home: Path | None = None) -> None:
    """Called by the human's commands: a decision ends an open pause. Never fails the command."""
    if command not in DECISIONS:
        return
    try:
        if current(home):
            _append(home, {"event": "resume", "ts": _iso(_now()), "by": command})
    except OSError:
        pass  # a pause that stays open is a measurement problem, not a reason to fail an approve


def _append(home: Path | None, record: dict) -> None:
    from . import lock  # lock imports events, which this module also uses

    target = path(home)
    target.parent.mkdir(parents=True, exist_ok=True)
    with lock.file_lock(target.parent / ".pauses.lock", record["event"]):
        with open(target, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(record) + "\n")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(ts: datetime) -> str:
    return ts.isoformat(timespec="milliseconds").replace("+00:00", "Z")
