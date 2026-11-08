"""Closed cards and how long they took, read from the event log of every registered vault.

One `Sample` per approved card: the agent's working time and the time it waited for the human,
plus the traits estimates group by. Read only -- nothing here writes. The rules (decided in the
v3.2 vault, dec-tiempos):

- Agent time is time In progress being worked, over every round. **A worker does one thing at a
  time**: a batch assigned at once (AFK) isn't parallel work, so a round really starts at the
  later of its assignment and the same worker's last action on another card. Subagents are
  different workers, so their work does run in parallel. A takeover doesn't cut the clock.
- After the last allowed eval attempt fails, the card waits for the human's `retry`: that stretch
  is review time, not the agent's.
- Review time is every stretch in "Proposal pending review", measured twice: on the calendar
  (`review_hours`, nights included) and active (`review_active_hours`), leaving out the hours the
  human was away -- their `histos pause` until their next decision (pauses.py).
- Time in Backlog or Blocked counts for neither.
- A card already In progress or in review when the log starts gives no sample (its start is
  unknown); a card whose colors changed by hand during its life is `approximate`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import events

MAX_ATTEMPTS = 3  # context.MAX_ATTEMPTS, kept here to avoid importing the command layer

_IN_PROGRESS = "In progress"
_REVIEW = "Proposal pending review"
_APPROVED = "Approved"
_WAITING = {None, "Backlog", "Blocked"}


@dataclass(frozen=True)
class Sample:
    vault: str
    card: str
    approved_at: datetime
    agent_hours: float
    review_hours: float          # calendar time waiting for the human
    review_active_hours: float   # the same, minus the human's pauses
    rounds: int
    rejections: int
    tier: str | None
    model: str | None
    dependencies: int
    worker_kind: str | None  # "parent", "sub" or None
    approximate: bool


@dataclass(frozen=True)
class Progress:
    """What an open card has accumulated so far -- for the critical path's "what's left"."""
    state: str | None
    agent_hours: float
    review_hours: float
    review_active_hours: float


@dataclass
class _Card:
    state: str | None = None
    incomplete: bool = False
    approximate: bool = False
    worker: str | None = None          # who works it now
    agent_open: datetime | None = None  # an open stretch of agent work
    wait_open: datetime | None = None   # waiting for the human's retry
    review_open: datetime | None = None
    agent_s: float = 0.0
    review_spans: list[tuple[datetime, datetime]] = field(default_factory=list)
    rounds: int = 0
    rejections: int = 0
    tier: str | None = None
    model: str | None = None
    model_after: str | None = None      # a model recorded after approval (late yes)
    deps: set[str] = field(default_factory=set)
    approved_at: datetime | None = None
    worker_kinds: set[str] = field(default_factory=set)


def _ts(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _kind(worker: str | None) -> str | None:
    if not worker:
        return None
    return "sub" if worker.startswith("sub-") else ("parent" if worker == "parent" else None)


class _Replay:
    """One pass over a vault's log, in order."""

    def __init__(self) -> None:
        self.cards: dict[str, _Card] = {}
        self.last_done: dict[str | None, tuple[datetime, str]] = {}  # worker -> (when, which card)

    def card(self, card_id: str) -> _Card:
        return self.cards.setdefault(card_id, _Card())

    def _close_agent(self, card_id: str, c: _Card, now: datetime) -> None:
        if c.agent_open is None:
            return
        start = c.agent_open
        done = self.last_done.get(c.worker)
        if done and done[1] != card_id and done[0] > start:
            start = min(done[0], now)  # the worker was busy with another card until then
        c.agent_s += max(0.0, (now - start).total_seconds())
        c.agent_open = None
        self.last_done[c.worker] = (now, card_id)
        if c.worker:
            c.worker_kinds.add(_kind(c.worker) or "")

    def event(self, e: dict) -> None:
        now = _ts(e["ts"])
        name, card_id, data = e["event"], e.get("card"), e.get("data") or {}
        if card_id:
            c = self.card(card_id)
            if name == "card_created":
                c.deps |= set(data.get("depends_on") or [])
            elif name == "dependency_added":
                c.deps |= set(data.get("depends_on") or [])
            elif name == "classified":
                c.tier = data.get("tier") or c.tier
            elif name == "model_approved":
                if c.approved_at is None:
                    c.model = data.get("model") or c.model
                elif c.model_after is None:
                    c.model_after = data.get("model")
            elif name == "assigned":
                if data.get("takeover") and c.agent_open is not None:
                    self._close_agent(card_id, c, now)  # the old worker's share counts
                    c.agent_open = now
                c.worker = e.get("worker")
            elif name == "proposal_refused":
                if c.agent_open is not None and data.get("attempt") == MAX_ATTEMPTS:
                    self._close_agent(card_id, c, now)
                    c.wait_open = now  # out of attempts: waiting for the human's retry
                else:
                    self.last_done[c.worker] = (now, card_id)
            elif name == "retry" and c.wait_open is not None:
                c.review_spans.append((c.wait_open, now))
                c.wait_open = None
                c.agent_open = now
        outside = name == "state_recomputed" and data.get("seen_outside_histos")
        for t in e.get("transitions") or []:
            self.transition(t["card"], t.get("from"), t.get("to"), now, outside)

    def transition(self, card_id: str, old: str | None, new: str | None, now: datetime, outside: bool) -> None:
        c = self.card(card_id)
        if c.state is None and old not in _WAITING:
            c.incomplete = True  # already under way when the log started
        if outside:
            c.approximate = True
        if new == _IN_PROGRESS and old != _IN_PROGRESS:
            c.agent_open = now
        if old == _IN_PROGRESS and new != _IN_PROGRESS:
            self._close_agent(card_id, c, now)
            if c.wait_open is not None:  # left In progress while waiting for a retry
                c.review_spans.append((c.wait_open, now))
                c.wait_open = None
        if new == _REVIEW and old != _REVIEW:
            c.review_open = now
            c.rounds += 1
        if old == _REVIEW and new != _REVIEW and c.review_open is not None:
            c.review_spans.append((c.review_open, now))
            c.review_open = None
            if new == "Backlog":
                c.rejections += 1
        if new == _APPROVED:
            c.approved_at = now
        c.state = new


def _replay(vault_root: Path) -> _Replay:
    replay = _Replay()
    for e in events.read(vault_root):
        replay.event(e)
    return replay


def _vault_id(vault_root: Path) -> str:
    try:
        info = json.loads((vault_root / events.LOG_DIR / events.VAULT_ID_FILENAME).read_text(encoding="utf-8"))
        return str(info["id"])
    except (OSError, ValueError, KeyError, TypeError):
        return str(vault_root.resolve())


def _pause_spans(home: Path | None, now: datetime) -> list[tuple[datetime, datetime]]:
    from . import pauses  # pauses -> events, like this module

    return [(p.start, p.end or now) for p in pauses.read(home)]


def _hours(spans: list[tuple[datetime, datetime]]) -> float:
    return sum(max(0.0, (end - start).total_seconds()) for start, end in spans) / 3600


def _active_hours(spans: list[tuple[datetime, datetime]], away: list[tuple[datetime, datetime]]) -> float:
    """Hours of `spans` that fall outside every `away` span."""
    total = 0.0
    for start, end in spans:
        seconds = max(0.0, (end - start).total_seconds())
        for a_start, a_end in away:
            overlap = (min(end, a_end) - max(start, a_start)).total_seconds()
            if overlap > 0:
                seconds -= overlap  # pauses never overlap each other: one pause at a time
        total += max(0.0, seconds)
    return total / 3600


def vault_samples(vault_root: Path, home: Path | None = None) -> list[Sample]:
    """One sample per card approved in this vault whose whole life is in its log. The human's
    pauses come from `home` (default: HISTOS_HOME or ~/.histos)."""
    replay = _replay(vault_root)
    vault = _vault_id(vault_root)
    away = _pause_spans(home, datetime.now(timezone.utc))
    result = []
    for card_id, c in replay.cards.items():
        if c.approved_at is None or c.incomplete or c.rounds == 0:
            continue
        kinds = c.worker_kinds - {""}
        result.append(Sample(
            vault=vault, card=card_id, approved_at=c.approved_at,
            agent_hours=c.agent_s / 3600, review_hours=_hours(c.review_spans),
            review_active_hours=_active_hours(c.review_spans, away),
            rounds=c.rounds, rejections=c.rejections, tier=c.tier,
            model=c.model or c.model_after, dependencies=len(c.deps),
            worker_kind="sub" if "sub" in kinds else ("parent" if "parent" in kinds else None),
            approximate=c.approximate,
        ))
    return sorted(result, key=lambda s: s.approved_at)


def open_progress(vault_root: Path, now: datetime | None = None, home: Path | None = None) -> dict[str, Progress]:
    """For each card that isn't approved: its state and the agent/review time it has so far."""
    now = now or datetime.now(timezone.utc)
    replay = _replay(vault_root)
    away = _pause_spans(home, now)
    result = {}
    for card_id, c in replay.cards.items():
        if c.state in (None, _APPROVED):
            continue
        agent = c.agent_s
        if c.agent_open is not None:
            agent += max(0.0, (now - c.agent_open).total_seconds())
        spans = list(c.review_spans)
        spans += [(start, now) for start in (c.wait_open, c.review_open) if start is not None]
        result[card_id] = Progress(state=c.state, agent_hours=agent / 3600, review_hours=_hours(spans),
                                   review_active_hours=_active_hours(spans, away))
    return result


def registered_vaults(home: Path | None = None) -> list[Path]:
    path = (home or events.home()) / events.REGISTRY_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = data.get("vaults") if isinstance(data, dict) else None
    return [Path(v["path"]) for v in entries or [] if isinstance(v, dict) and isinstance(v.get("path"), str)]


def samples(home: Path | None = None, skipped: list[str] | None = None) -> list[Sample]:
    """Samples from every registered vault, each vault once. Vaults that are gone or whose log
    can't be read are left out and named in `skipped` (when given) -- never an error."""
    seen: set[str] = set()
    result: list[Sample] = []
    for vault_root in registered_vaults(home):
        if not events.log_path(vault_root).exists():
            if skipped is not None:
                skipped.append(f"{vault_root}: no event log there")
            continue
        vault = _vault_id(vault_root)
        if vault in seen:
            continue
        seen.add(vault)
        try:
            result += vault_samples(vault_root, home)
        except (OSError, ValueError, KeyError) as e:
            if skipped is not None:
                skipped.append(f"{vault_root}: {e}")
    return sorted(result, key=lambda s: s.approved_at)
