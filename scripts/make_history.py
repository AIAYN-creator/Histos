"""Synthetic vaults with known timings, to check that estimates and `--coverage` say what they should.

Writes, inside ONE new (or empty) folder:

    <out>/home/vaults.json         the registry -- point HISTOS_HOME at <out>/home
    <out>/home/pauses.jsonl        the human's night pauses
    <out>/vaults/<name>/           each vault: project.canvas (from the real init), content/<id>.md
                                   for every card, and .histos/{events.jsonl,vault.json,colors.json}

The event logs have exactly the shape the real CLI writes (card_created, classified, assigned,
proposal_refused, proposed, approved, rejected, retry), with the `transitions` of a session on its
last event. Every closed card's times come from a "recipe" per group (tier, type, dependencies)
drawn with a fixed seed, so the same arguments always give the same history -- and the generator
returns what each card was MEANT to take (`Truth`), so a test never has to trust the code under
test to know the right answer.

Besides plain cards it writes: AFK batches (several cards assigned at once to one worker, worked
one at a time), parallel subagents, rejections, exhausted attempts followed by `retry`, night
pauses, one vault whose ids have no prefixes, and some OPEN cards (Backlog, In progress, in
review, Blocked) so `histos status` and `histos estimate --coverage` have cards to estimate.

It refuses to write anywhere but an empty or new folder that is neither your home folder nor
~/.histos nor your HISTOS_HOME, and it never touches anything outside that folder.

    python scripts/make_history.py <out_folder> [--cards N] [--vaults V] [--seed S]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
try:
    import histos  # noqa: F401
except ImportError:  # run from a checkout without installing it
    sys.path.insert(0, str(ROOT / "src"))

from histos import canvas, events, frontmatter, operations  # noqa: E402

B, P, R, A, BL = "Backlog", "In progress", "Proposal pending review", "Approved", "Blocked"
_COLORS = {B: canvas.BACKLOG, P: canvas.IN_PROGRESS, R: canvas.PENDING_REVIEW, A: canvas.APPROVED, BL: canvas.BLOCKED}

START = datetime(2025, 3, 3, tzinfo=timezone.utc)
MIN, HOUR, DAY = 60, 3600, 86400
NIGHT_START = 22 * HOUR           # the human goes away at 22:00 ...
NIGHT_FALLBACK = 9 * HOUR + 1800  # ... and, if nobody decides anything soon, is back 9.5 h later

# --- Recipes: how long a card of each group takes ------------------------------------------------
TYPES = [("cod", 0.50), ("dec", 0.20), ("doc", 0.15), ("rel", 0.15)]
TIERS_OF_TYPE = {
    "cod": [("exec", 0.8), ("fast", 0.1), ("reasoning", 0.1)],
    "dec": [("reasoning", 0.8), ("exec", 0.2)],
    "doc": [("fast", 0.5), ("exec", 0.5)],
    "rel": [("fast", 1.0)],
}
AGENT_MINUTES = {"fast": (5, 15), "exec": (20, 40), "reasoning": (45, 120)}  # per tier
AGENT_TYPE_FACTOR = {"cod": 1.0, "dec": 1.4, "doc": 0.8, "rel": 0.7}
AGENT_DEPS_EXTRA = {0: 0, 1: 10, 2: 25}  # minutes added by the dependency bucket (0, 1, 2+)
REVIEW_HOURS = {"cod": (2, 6), "dec": (1, 4), "doc": (0.5, 2), "rel": (0.5, 1.5)}  # per type (the human)
SYLLABLES = ["ka", "lo", "mi", "ru", "te", "ba", "no", "si", "vu", "ge", "pa", "do"]


class UnsafeOutput(ValueError):
    """The output folder is not one we may write into."""


@dataclass
class Truth:
    """What one closed card was meant to take. Hours come from the spans actually written."""
    vault: str                # the vault's folder name
    card: str
    type: str | None          # as estimates read it: None in the vault without prefixes
    tier: str
    deps: int
    kind: str                 # plain | night | rejected | exhausted | afk | subagent
    approved_at: datetime
    agent_spans: list = field(default_factory=list)
    review_spans: list = field(default_factory=list)
    rejections: int = 0
    review_active_hours: float = 0.0  # filled in when the pauses are known

    @property
    def agent_hours(self) -> float:
        return _hours(self.agent_spans)

    @property
    def review_hours(self) -> float:
        return _hours(self.review_spans)

    @property
    def group(self) -> tuple:
        return (self.tier, self.type, min(self.deps, 2))


@dataclass
class OpenCard:
    vault: str
    card: str
    state: str
    type: str | None
    tier: str | None          # None: not classified yet
    deps: int


@dataclass
class Info:
    out: Path
    home: Path
    vaults: list              # [Path]
    names: list               # folder names, same order
    prefixed: dict            # name -> bool
    truths: list
    opens: list
    pauses: list              # [(start, end, by)]

    def vault_of(self, name: str) -> Path:
        return self.vaults[self.names.index(name)]


def _seconds(spans) -> float:
    return sum((end - start).total_seconds() for start, end in spans)


def _hours(spans) -> float:
    return _seconds(spans) / 3600


def _overlap_seconds(spans, away) -> float:
    total = 0.0
    for s, e in spans:
        for a, b in away:
            total += max(0.0, (min(e, b) - max(s, a)).total_seconds())
    return total


def iso(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%S.000Z")


# --- Safety -------------------------------------------------------------------------------------

def check_output(out: Path) -> Path:
    """The resolved output folder, or UnsafeOutput. Only an empty or new folder qualifies, and never
    the user's home, ~/.histos, their HISTOS_HOME, nor anything inside or around them."""
    out = Path(out).expanduser().resolve()
    home = Path.home().resolve()
    protected = [home, home / ".histos"]
    if os.environ.get(events.HOME_ENV):
        protected.append(Path(os.environ[events.HOME_ENV]).expanduser().resolve())
    for p in protected:
        if out == p or p.is_relative_to(out) or out.is_relative_to(home / ".histos"):
            raise UnsafeOutput(f"refusing to write in {out}: it is, contains or lies inside {p}, "
                               "which holds real data. Pick a new folder.")
    if out.exists():
        if not out.is_dir():
            raise UnsafeOutput(f"refusing to write in {out}: it exists and is not a folder")
        if any(out.iterdir()):
            raise UnsafeOutput(f"refusing to write in {out}: it is not empty. Pick a new or empty folder.")
    return out


@contextmanager
def _no_events():
    """init_vault records `vault_created` through the event layer; this generator writes the logs itself."""
    real = events.emit
    events.emit = lambda *a, **k: None
    try:
        yield
    finally:
        events.emit = real


# --- The history ---------------------------------------------------------------------------------

class History:
    """All the vaults of one generated folder, plus the human's pauses (one person, not per vault)."""

    def __init__(self, out: Path, seed: int = 1) -> None:
        self.out = check_output(out)
        self.rng = random.Random(seed)
        self.seed = seed
        self.writers: list[VaultWriter] = []
        self.truths: list[Truth] = []
        self.opens: list[OpenCard] = []
        self.night_days: set[int] = set()
        self.all_days: set[int] = set()
        self._used: set[datetime] = set()

    def vault(self, name: str, prefixed: bool = True) -> "VaultWriter":
        w = VaultWriter(self, name, prefixed)
        self.writers.append(w)
        return w

    def unique(self, ts: datetime) -> datetime:
        """Approval times are unique across the whole history: no ties when estimates sort by them."""
        while ts in self._used:
            ts += timedelta(seconds=1)
        self._used.add(ts)
        return ts

    def finish(self, idle_nights: float = 0.3) -> Info:
        pauses = self._pauses(idle_nights)
        away = [(s, e) for s, e, _ in pauses]
        for t in self.truths:
            t.review_active_hours = (_seconds(t.review_spans) - _overlap_seconds(t.review_spans, away)) / 3600
        (self.out / "vaults").mkdir(parents=True, exist_ok=True)
        home = self.out / "home"
        home.mkdir(parents=True, exist_ok=True)
        registry = []
        for w in self.writers:
            w.write()
            registry.append({"id": w.vault_id, "path": str(w.root.resolve())})
        (home / events.REGISTRY_FILENAME).write_text(
            json.dumps({"vaults": registry}, indent=2) + "\n", encoding="utf-8")
        lines = []
        for s, e, by in pauses:
            lines.append({"event": "pause", "ts": iso(s)})
            lines.append({"event": "resume", "ts": iso(e), "by": by})
        (home / "pauses.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
        return Info(out=self.out, home=home, vaults=[w.root for w in self.writers],
                    names=[w.name for w in self.writers], prefixed={w.name: w.prefixed for w in self.writers},
                    truths=self.truths, opens=self.opens, pauses=pauses)

    def _pauses(self, idle_nights: float) -> list:
        """One pause per night: 22:00 until the human's first decision after it (approve, reject or
        retry, in any vault) -- as `histos pause` plus the next decision would have recorded it."""
        decisions = sorted((ts, e["event"]) for w in self.writers for ts, _, e in w.events
                           if e["event"] in ("approved", "rejected", "retry"))
        nights = set(self.night_days)
        for day in sorted(self.all_days - nights):
            if self.rng.random() < idle_nights:
                nights.add(day)
        result = []
        for day in sorted(nights):
            start = START + timedelta(days=day, seconds=NIGHT_START)
            end, by = start + timedelta(seconds=NIGHT_FALLBACK), "approve"
            for ts, name in decisions:
                if ts > start:
                    if ts - start <= timedelta(seconds=12 * HOUR):
                        end, by = ts, {"approved": "approve", "rejected": "reject", "retry": "retry"}[name]
                    break
            result.append((start, end, by))
        return result


class VaultWriter:
    def __init__(self, history: History, name: str, prefixed: bool) -> None:
        self.h, self.name, self.prefixed = history, name, prefixed
        self.root = history.out / "vaults" / name
        self.vault_id = str(uuid.UUID(int=history.rng.getrandbits(128), version=4))
        self.events: list[tuple] = []          # (ts, seq, event dict)
        self.cards: dict[str, dict] = {}       # id -> {state, tier, deps, type}
        self.closed: list[tuple] = []          # (approved_at, id)
        self.day = 0                           # the next free day; one unit of work per day
        self._counters: dict[str, int] = {}
        self._names: set[str] = set()

    # ---- events, shaped as the CLI writes them ----
    def _emit(self, ts, name, card=None, worker=None, data=None, transitions=None, actor="agent"):
        e = {"v": 1, "ts": iso(ts), "event": name, "card": card, "actor": actor, "worker": worker}
        if data:
            e["data"] = data
        if transitions:
            e["transitions"] = [{"card": c, "from": f, "to": t} for c, f, t in transitions]
        self.events.append((ts, len(self.events), e))

    def _size(self) -> dict:
        return {"words": self.h.rng.randint(80, 900), "lines": self.h.rng.randint(8, 80)}

    def _assign(self, ts, card, worker, transitions=True):
        self._emit(ts, "assigned", card, worker, {"by": "agent", "takeover": False, "previous_worker": None},
                   [(card, B, P)] if transitions else None)
        self.cards[card]["state"] = P

    def _propose(self, ts, card, worker, attempt=1):
        self._emit(ts, "proposed", card, worker, {"attempt": attempt, **self._size()}, [(card, P, R)])
        self.cards[card]["state"] = R

    def _refuse(self, ts, card, worker, attempt):
        self._emit(ts, "proposal_refused", card, worker, {"attempt": attempt, "failed": ["word_count"], **self._size()})

    def _approve(self, ts, card):
        self._emit(ts, "approved", card, None, self._size(), [(card, R, A)], actor="human")
        self.cards[card]["state"] = A

    def _reject(self, ts, card, worker):
        self._emit(ts, "rejected", card, worker, {"feedback": "needs another pass"}, [(card, R, B)], actor="human")
        self.cards[card]["state"] = B

    def _retry(self, ts, card, worker):
        self._emit(ts, "retry", card, worker, actor="human")

    # ---- cards ----
    def _new_id(self, type_: str) -> str:
        if self.prefixed:
            n = self._counters[type_] = self._counters.get(type_, 0) + 1
            return f"{type_}-{n:03d}"
        while True:
            name = "".join(self.h.rng.choice(SYLLABLES) for _ in range(4))
            if name not in self._names:
                self._names.add(name)
                return name

    def _weighted(self, options):
        return self.h.rng.choices([o for o, _ in options], [w for _, w in options])[0]

    def draw(self, created: datetime, *, latent=None, tier=None, deps=None):
        """A new card's id, latent type (drives its recipe), tier and dependencies. Dependencies are
        cards already approved when it is created, so it starts in Backlog, never Blocked."""
        rng = self.h.rng
        latent = latent or self._weighted(TYPES)
        tier = tier or self._weighted(TIERS_OF_TYPE[latent])
        want = deps if deps is not None else self._weighted([(0, 0.5), (1, 0.3), (2, 0.12), (3, 0.08)])
        ready = [cid for at, cid in self.closed if at < created]
        chosen = rng.sample(ready, min(want, len(ready)))
        return self._new_id(latent), latent, tier, chosen

    def _create(self, ts, card, latent, tier, deps, *, classify=True, state=B):
        self.cards[card] = {"state": state, "tier": tier if classify else None, "deps": list(deps),
                            "type": latent if self.prefixed else None, "latent": latent}
        self._emit(ts, "card_created", card, None, {"title": card, "depends_on": list(deps)},
                   [(card, None, state)])
        if classify:
            self._emit(ts + timedelta(seconds=5 * MIN), "classified", card, None,
                       {"tier": tier, "previous_tier": None, "model_cleared": False})

    def agent_seconds(self, latent, tier, deps) -> int:
        lo, hi = AGENT_MINUTES[tier]
        minutes = self.h.rng.uniform(lo, hi) * AGENT_TYPE_FACTOR[latent] + AGENT_DEPS_EXTRA[min(deps, 2)]
        return max(1, round(minutes)) * MIN

    def review_seconds(self, latent) -> int:
        lo, hi = REVIEW_HOURS[latent] if self.prefixed else (1, 5)
        return round(self.h.rng.uniform(lo * 60, hi * 60)) * MIN

    def _truth(self, card, kind, approved, agent_spans, review_spans, rejections=0) -> Truth:
        c = self.cards[card]
        t = Truth(vault=self.name, card=card, type=c["type"], tier=c["tier"], deps=len(c["deps"]), kind=kind,
                  approved_at=approved, agent_spans=agent_spans, review_spans=review_spans, rejections=rejections)
        self.h.truths.append(t)
        self.closed.append((approved, card))
        return t

    def _day_start(self, span=1) -> datetime:
        base = START + timedelta(days=self.day)
        self.h.all_days.update(range(self.day, self.day + span))
        self.day += span
        return base

    def _jitter(self, minutes) -> timedelta:
        return timedelta(seconds=self.h.rng.randint(0, minutes) * MIN)

    # ---- units of work: each starts on its own day, so the one worker is never double-booked ----
    def plain(self, *, night=False, agent_s=None, review_s=None, **draw) -> Truth:
        """One card on its own day. `agent_s` / `review_s` replace the recipe's drawn seconds."""
        base = self._day_start()
        if night:
            self.h.night_days.add(self.day - 1)
        created = base + timedelta(hours=8) + self._jitter(20)
        card, latent, tier, deps = self.draw(created, **draw)
        self._create(created, card, latent, tier, deps)
        assigned = base + timedelta(hours=17 if night else 9) + self._jitter(60 if night else 30)
        agent = agent_s or self.agent_seconds(latent, tier, len(deps))
        proposed = assigned + timedelta(seconds=agent)
        if night:  # proposed in the evening, approved the next morning, after the human's night away
            approved = base + timedelta(days=1, hours=8) + self._jitter(120)
        else:
            approved = proposed + timedelta(seconds=review_s or self.review_seconds(latent))
        approved = self.h.unique(approved)
        self._assign(assigned, card, "parent")
        self._propose(proposed, card, "parent")
        self._approve(approved, card)
        return self._truth(card, "night" if night else "plain", approved, [(assigned, proposed)], [(proposed, approved)])

    def rejected(self, **draw) -> Truth:
        base = self._day_start(span=2)
        created = base + timedelta(hours=8)
        card, latent, tier, deps = self.draw(created, **draw)
        self._create(created, card, latent, tier, deps)
        a1 = base + timedelta(hours=9)
        p1 = a1 + timedelta(seconds=self.agent_seconds(latent, tier, len(deps)))
        rejected_at = p1 + timedelta(seconds=self.h.rng.randint(1, 3) * HOUR)
        a2 = base + timedelta(days=1, hours=9) + self._jitter(30)
        p2 = a2 + timedelta(seconds=max(1, self.agent_seconds(latent, tier, len(deps)) // 2 // MIN) * MIN)
        approved = self.h.unique(p2 + timedelta(seconds=self.review_seconds(latent)))
        self._assign(a1, card, "parent")
        self._propose(p1, card, "parent")
        self._reject(rejected_at, card, "parent")
        self._assign(a2, card, "parent")
        self._propose(p2, card, "parent")
        self._approve(approved, card)
        return self._truth(card, "rejected", approved, [(a1, p1), (a2, p2)],
                           [(p1, rejected_at), (p2, approved)], rejections=1)

    def exhausted(self, **draw) -> Truth:
        """Three failed proposals, the human's `retry` hours later, then a good one: the wait for
        the retry is review time, not the agent's."""
        base = self._day_start()
        created = base + timedelta(hours=8)
        card, latent, tier, deps = self.draw(created, **draw)
        self._create(created, card, latent, tier, deps)
        assigned = base + timedelta(hours=9)
        first = max(3, self.agent_seconds(latent, tier, len(deps)) // MIN) * MIN
        step = first // 3 // MIN * MIN or MIN
        refusals = [assigned + timedelta(seconds=step * n) for n in (1, 2, 3)]
        retry = refusals[-1] + timedelta(seconds=self.h.rng.randint(1, 5) * HOUR)
        proposed = retry + timedelta(seconds=self.h.rng.randint(10, 30) * MIN)
        approved = self.h.unique(proposed + timedelta(seconds=self.review_seconds(latent)))
        self._assign(assigned, card, "parent")
        for n, ts in enumerate(refusals, start=1):
            self._refuse(ts, card, "parent", n)
        self._retry(retry, card, "parent")
        self._propose(proposed, card, "parent", attempt=1)
        self._approve(approved, card)
        return self._truth(card, "exhausted", approved, [(assigned, refusals[-1]), (retry, proposed)],
                           [(refusals[-1], retry), (proposed, approved)])

    def afk(self, n: int = 4, *, one_by_one=False, agent_minutes=None, review_hours=None, **draw) -> list[Truth]:
        """`n` cards handed to the parent at once and worked one after the other. With
        `one_by_one`, the same work with each card assigned only when the worker gets to it."""
        base = self._day_start()
        cards, started = [], base + timedelta(hours=9)
        for i in range(n):
            created = base + timedelta(hours=8, minutes=i)
            card, latent, tier, deps = self.draw(created, **draw)
            self._create(created, card, latent, tier, deps)
            cards.append((card, latent, tier, deps))
        agents = [(agent_minutes[i] * MIN) if agent_minutes else self.agent_seconds(c[1], c[2], len(c[3]))
                  for i, c in enumerate(cards)]
        done, cursor = [], started
        for secs in agents:
            cursor += timedelta(seconds=secs)
            done.append(cursor)
        if not one_by_one:
            for card, *_ in cards[:-1]:
                self._assign(started, card, "parent", transitions=False)
            last = cards[-1][0]
            self._emit(started, "assigned", last, "parent", {"by": "agent", "takeover": False, "previous_worker": None},
                       [(c[0], B, P) for c in cards])
            for card, *_ in cards:
                self.cards[card]["state"] = P
        starts = [started] + done[:-1]
        reviewed = base + timedelta(days=1, hours=9)
        truths = []
        for i, (card, latent, *_rest) in enumerate(cards):
            if one_by_one:
                self._assign(starts[i], card, "parent")
            self._propose(done[i], card, "parent")
            wait = (review_hours[i] * HOUR) if review_hours else (reviewed + timedelta(minutes=10 * i) - done[i]).total_seconds()
            approved = self.h.unique(done[i] + timedelta(seconds=round(wait)))
            self._approve(approved, card)
            truths.append(self._truth(card, "afk", approved, [(starts[i], done[i])], [(done[i], approved)]))
        return truths

    def subagents(self, n: int = 3, **draw) -> list[Truth]:
        """`n` cards, each handed to its own subagent at the same moment: they really work in parallel."""
        base = self._day_start()
        t0 = base + timedelta(hours=9)
        truths = []
        for i in range(n):
            created = base + timedelta(hours=8, minutes=i)
            card, latent, tier, deps = self.draw(created, **draw)
            self._create(created, card, latent, tier, deps)
            worker = f"sub-{i + 1}"
            proposed = t0 + timedelta(seconds=self.agent_seconds(latent, tier, len(deps)))
            approved = self.h.unique(proposed + timedelta(seconds=self.review_seconds(latent)))
            self._assign(t0, card, worker)
            self._propose(proposed, card, worker)
            self._approve(approved, card)
            truths.append(self._truth(card, "subagent", approved, [(t0, proposed)], [(proposed, approved)]))
        return truths

    # ---- open cards ----
    def open_card(self, state: str, backlog=(), **draw) -> str:
        """One card still alive: B (Backlog), "unclassified" (Backlog, no tier), P, R, or BL (Blocked
        on an open Backlog card from `backlog`). Created after everything closed so far."""
        created = START + timedelta(days=self.day, hours=8, minutes=len(self.h.opens))
        card, latent, tier, deps = self.draw(created, **draw)
        if state == BL and backlog:
            self._create(created, card, latent, tier, deps + [backlog[0]], state=BL)
        elif state in (P, R):
            self._create(created, card, latent, tier, deps)
            self._assign(created + timedelta(hours=1), card, "parent")
            if state == R:
                self._propose(created + timedelta(hours=2), card, "parent")
        else:
            self._create(created, card, latent, tier, deps, classify=state != "unclassified")
        c = self.cards[card]
        self.h.opens.append(OpenCard(self.name, card, c["state"], c["type"], c["tier"], len(c["deps"])))
        return card

    def open_cards(self, count: int) -> None:
        """A mix of open cards: Backlog (classified or not), In progress, in review and Blocked."""
        self._day_start()
        cycle = [B, B, P, R, "unclassified", BL, B, P]
        backlog: list[str] = []
        for i in range(count):
            state = cycle[i % len(cycle)]
            card = self.open_card(state, backlog)
            if self.cards[card]["state"] == B and state == B:
                backlog.append(card)

    # ---- writing the vault ----
    def write(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with _no_events():
            init = getattr(operations.init_vault, "__wrapped__", operations.init_vault)
            init(self.root)
        data = canvas.load(self.root)
        for i, (card, c) in enumerate(self.cards.items()):
            node = canvas.add_card_node(data, card, _COLORS[c["state"]])
            node["x"], node["y"] = (i % 25) * 340, 200 + (i // 25) * 140
            for dep in c["deps"]:
                canvas.add_edge(data, dep, card)
            meta = dict(frontmatter.DEFAULT_META)
            body = f"# {card}\n\nSynthetic card {card}.\n\n"
            meta["description"] = f"Synthetic card {card}."
            if c["tier"]:
                meta["proposed_tier"] = c["tier"]
            if c["state"] == P or c["state"] == R:
                meta[operations.WORKER_KEY] = "parent"
            meta[frontmatter.CONTENT_HASH_KEY] = frontmatter.body_hash(body)
            frontmatter.write(canvas.card_file_path(self.root, node), meta, body)
        canvas.save(self.root, data)
        log_dir = self.root / events.LOG_DIR
        log_dir.mkdir(exist_ok=True)
        ordered = [e for _, _, e in sorted(self.events, key=lambda x: (x[0], x[1]))]
        (log_dir / events.LOG_FILENAME).write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in ordered), encoding="utf-8", newline="\n")
        (log_dir / events.VAULT_ID_FILENAME).write_text(
            json.dumps({"id": self.vault_id, "created": iso(START)}, indent=2) + "\n", encoding="utf-8")
        (log_dir / events.COLORS_FILENAME).write_text(
            json.dumps({cid: _COLORS[c["state"]] for cid, c in self.cards.items()}, sort_keys=True) + "\n",
            encoding="utf-8")


# --- The default mix -------------------------------------------------------------------------------

def generate(out, cards: int = 200, vaults: int = 3, seed: int = 1, *, special: bool = True,
             nights: float = 0.15, idle_nights: float = 0.3, open_per_vault: int | None = None) -> Info:
    """`cards` closed cards spread over `vaults` vaults (the last one without id prefixes when there
    are two or more), plus open cards. `special=False` leaves only plain cards (and night ones,
    in the proportion `nights`). Returns what every card was meant to take."""
    if cards < 1 or vaults < 1:
        raise ValueError("--cards and --vaults must be at least 1")
    h = History(out, seed)
    mix = [("plain", 1 - nights), ("night", nights)]
    if special:
        mix = [("plain", 0.50 - nights / 2), ("night", nights), ("rejected", 0.07), ("exhausted", 0.05),
               ("afk", 0.12), ("subagents", 0.06)]
        mix = [(k, max(w, 0.0)) for k, w in mix]
    for v in range(vaults):
        w = h.vault(f"vault-{chr(ord('a') + v) if v < 26 else v}", prefixed=not (vaults >= 2 and v == vaults - 1))
        target = cards // vaults + (1 if v < cards % vaults else 0)
        done = 0
        while done < target:
            kind = h.rng.choices([k for k, _ in mix], [x for _, x in mix])[0]
            if kind == "plain":
                w.plain(); done += 1
            elif kind == "night":
                w.plain(night=True); done += 1
            elif kind == "rejected":
                w.rejected(); done += 1
            elif kind == "exhausted":
                w.exhausted(); done += 1
            elif kind == "afk":
                n = min(h.rng.randint(3, 5), target - done)
                w.afk(n); done += n
            else:
                n = min(h.rng.randint(2, 3), target - done)
                w.subagents(n); done += n
        w.open_cards(open_per_vault if open_per_vault is not None else max(8, target // 10))
    return h.finish(idle_nights)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Generate synthetic Histos vaults with known timings.")
    ap.add_argument("out", help="a NEW or EMPTY folder to write everything into")
    ap.add_argument("--cards", type=int, default=200, help="closed cards across all vaults (default 200)")
    ap.add_argument("--vaults", type=int, default=3, help="number of vaults (default 3)")
    ap.add_argument("--seed", type=int, default=1, help="random seed (default 1)")
    args = ap.parse_args(argv)
    try:
        info = generate(args.out, args.cards, args.vaults, args.seed)
    except (UnsafeOutput, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"Wrote {len(info.truths)} closed and {len(info.opens)} open cards in {len(info.vaults)} vaults, "
          f"{len(info.pauses)} night pauses.\n")
    print("Point Histos at it (never at your real ~/.histos):")
    print(f'  PowerShell:  $env:HISTOS_HOME = "{info.home}"')
    print(f'  bash:        export HISTOS_HOME="{info.home}"')
    print("then, inside any vault:")
    for p in info.vaults:
        print(f"  cd {p}")
    print("  histos status\n  histos estimate --coverage")
    return 0


if __name__ == "__main__":
    sys.exit(main())
