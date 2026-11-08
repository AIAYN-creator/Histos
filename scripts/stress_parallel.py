"""Stress test: several agents (parent and subagents) hammering one vault at the same time.

Works on a throwaway vault in a temp folder -- never on a real one. N workers run R rounds
each; every operation is a separate `histos` process, as with real agents, so processes race
for project.canvas and the vault lock. Then it checks the invariants:

- project.canvas is valid JSON and `histos validate` passes (frontmatter included);
- no lost update: each worker owns some cards, and every card ends exactly as its owner's last
  successful operation left it (state, description, proposed model);
- contested cards (every worker tries to `assign` them) end with exactly one winner;
- no *.tmp left in the vault, the lock file is empty, and no command crashed (traceback);
- the event log (.histos/events.jsonl) matches what really happened: no broken line, exactly
  one event per successful operation (a refused one records nothing -- except a draft that fails
  the evals, which records `proposal_refused`);
- the log is in time order (50 ms of tolerance: events are stamped when emitted, a bit before
  the write), and its `transitions`, applied in file order to the colors right after setup, give
  exactly the final colors of project.canvas;
- each contested card has as many non-takeover `assigned` events as winners the script tracked;
- the critical path (cod-camino) is the right one: recomputed on the final board, the arrows
  carrying the Histos color are exactly those of the path that nobody else colored, and they
  are the ones noted in .histos/critical-path.json (every noted arrow exists with the Histos
  color, no arrow has it without being noted);
- repainting is not an event: the log has no event of a type outside events.EVENTS.

It sets HISTOS_HOME to a temp folder, so it never writes to the real ~/.histos.

    python scripts/stress_parallel.py [--workers 8] [--rounds 20] [--seed 1] [--keep]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from histos import canvas, config, critical, events, frontmatter, operations  # noqa: E402

CARDS_PER_WORKER = 4
CONTESTED = 3
TS_TOLERANCE_S = 0.05
# Events one successful operation records each (the script's ops -> these names). `classify` is
# not counted: it only records when something changed.
COUNTED_EVENTS = frozenset({"assigned", "described", "model_proposed", "proposed", "proposal_refused"})
GOOD_DRAFT = "Un borrador completo con bastantes palabras para pasar las evals sin problema.\n"
BAD_DRAFT = "Falta esto: TODO\n"


@dataclass
class Expected:
    color: str = canvas.BACKLOG
    description: str | None = None
    proposed_model: str | None = None
    failures: int = 0


@dataclass
class Report:
    ops: Counter = field(default_factory=Counter)
    ok: Counter = field(default_factory=Counter)
    refused: Counter = field(default_factory=Counter)
    crashes: list[str] = field(default_factory=list)
    busy: int = 0
    events: Counter = field(default_factory=Counter)  # events the successful operations should have recorded
    lock: threading.Lock = field(default_factory=threading.Lock)


def histos(vault: Path, *argv: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SRC), PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-m", "histos", *argv], cwd=vault, env=env,
                          capture_output=True, text=True, encoding="utf-8")


def make_vault(root: Path, workers: int) -> tuple[Path, dict[str, list[str]], list[str]]:
    vault = root / "vault"
    vault.mkdir()
    assert histos(vault, "init").returncode == 0
    owned = {f"w{w}": [f"w{w}-c{i}" for i in range(CARDS_PER_WORKER)] for w in range(workers)}
    contested = [f"shared{i}" for i in range(CONTESTED)]
    # Dependencies, so there is a critical path: even workers get one chain of all their cards,
    # odd ones two pairs. A card with an open dependency starts Blocked (and stays so: the
    # script never approves), so only the head of each chain moves.
    after: dict[str, str] = {}
    for w, cards in enumerate(owned.values()):
        for i in range(1, len(cards)):
            if w % 2 == 0 or i % 2 == 1:
                after[cards[i]] = cards[i - 1]
    for card_id in [c for cards in owned.values() for c in cards] + contested:
        argv = ["add-card", card_id, "--title", card_id]
        if card_id in after:
            argv += ["--depends-on", after[card_id], "--authorized"]
        assert histos(vault, *argv).returncode == 0
    return vault, owned, contested


def worker(vault: Path, name: str, cards: list[str], contested: list[str], rounds: int, seed: int,
           expected: dict[str, Expected], wins: dict[str, list[str]], report: Report, drafts: Path) -> None:
    rng = random.Random(f"{seed}-{name}")
    good, bad = drafts / f"{name}-good.md", drafts / f"{name}-bad.md"
    good.write_text(GOOD_DRAFT, encoding="utf-8")
    bad.write_text(BAD_DRAFT, encoding="utf-8")

    def expect(event: str) -> None:
        with report.lock:
            report.events[event] += 1

    def call(op: str, *argv: str) -> subprocess.CompletedProcess:
        result = histos(vault, *argv)
        with report.lock:
            report.ops[op] += 1
            if "Traceback" in result.stderr:
                report.crashes.append(f"{name} {' '.join(argv)}:\n{result.stderr}")
            if "the vault is busy" in result.stderr:
                report.busy += 1
            (report.ok if result.returncode == 0 else report.refused)[op] += 1
        return result

    for round_no in range(rounds):
        card = rng.choice(cards)
        exp = expected[card]
        # Only operations that make sense for the card's state, so every round does something.
        possible = ["read", "contest"]
        if exp.color == canvas.BACKLOG:
            possible += ["assign", "assign"]
        if exp.color != canvas.PENDING_REVIEW:
            possible += ["describe", "model", "classify"]
        if exp.color == canvas.IN_PROGRESS:
            possible += ["propose", "propose"]
        op = rng.choice(possible)
        if op == "assign" and exp.color == canvas.BACKLOG:
            if call("assign", "assign", card, "--worker", name).returncode == 0:
                exp.color = canvas.IN_PROGRESS
                expect("assigned")
        elif op == "describe" and exp.color != canvas.PENDING_REVIEW:
            text = f"{name} round {round_no}"
            if call("describe", "describe", card, "--text", text).returncode == 0:
                exp.description = text
                expect("described")
        elif op == "model" and exp.color != canvas.PENDING_REVIEW:
            model = f"model-{name}-{round_no}"
            if call("model", "model", card, "--proposed", model).returncode == 0:
                exp.proposed_model = model
                expect("model_proposed")
        elif op == "classify" and exp.color != canvas.PENDING_REVIEW:
            result = call("classify", "classify", card)
            if result.returncode == 0 and "tier changed" in result.stderr:
                exp.proposed_model = None  # a tier change legitimately clears the models
        elif op == "propose" and exp.color == canvas.IN_PROGRESS:
            draft = bad if rng.random() < 0.4 else good
            result = call("propose", "propose", card, "--file", str(draft))
            if result.returncode == 0:
                exp.color = canvas.PENDING_REVIEW
                expect("proposed")
            elif "not proposed" in result.stderr:  # failed the evals: exits 1 but records it
                exp.failures += 1
                expect("proposal_refused")
        elif op == "read":
            call("context", "context", card, "--for-subagent") if exp.color == canvas.IN_PROGRESS \
                else call("status", "status")
        elif op == "contest":
            target = rng.choice(contested)
            result = call("assign", "assign", target, "--worker", name)
            if result.returncode == 0 and "already yours" not in result.stdout:  # re-assigning your own is a no-op
                expect("assigned")
                with report.lock:
                    wins.setdefault(target, []).append(name)


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def check_events(vault: Path, report: Report, setup_events: int, initial: dict[str, str],
                 contested: list[str], wins: dict[str, list[str]]) -> list[str]:
    """The event log against what the workers really did. `setup_events` is how many events the
    setup (init, add-card) recorded: only what comes after belongs to the workers' phase."""
    problems = []
    path = events.log_path(vault)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if text and not text.endswith("\n"):
        problems.append("events: the log doesn't end with a newline (cut-off write)")
    log = []
    for n, line in enumerate(text.split("\n"), start=1):
        if not line:
            continue
        try:
            log.append(json.loads(line))
        except ValueError:
            problems.append(f"events: line {n} is not valid JSON (interleaved or cut-off write): {line[:80]!r}")
    errors, warnings = events.check_log(vault)
    problems += errors + warnings
    if problems:
        return problems

    ours = log[setup_events:]
    got = Counter(e["event"] for e in ours if e["event"] in COUNTED_EVENTS)
    for name in sorted(COUNTED_EVENTS):
        if got[name] != report.events[name]:
            problems.append(f"events: {got[name]} '{name}' recorded, but {report.events[name]} operations "
                            f"succeeded (lost or duplicated event)")
    outside = [e for e in ours if (e.get("data") or {}).get("seen_outside_histos")]
    if outside:
        problems.append(f"events: {len(outside)} state_recomputed 'seen outside histos', but nothing "
                        f"touched the vault outside histos")

    # Time order: file order is the order the vault lock granted the writes.
    for before, after in zip(log, log[1:]):
        if (_parse_ts(before["ts"]) - _parse_ts(after["ts"])).total_seconds() > TS_TOLERANCE_S:
            problems.append(f"events: out of order: {before['ts']} ({before['event']}) is followed by "
                            f"{after['ts']} ({after['event']})")
            break

    # The transitions rebuild the board: from the colors after setup to the final ones.
    by_name = {name: color for color, name in operations.STATE_NAMES.items()}
    board = dict(initial)
    for n, e in enumerate(ours, start=setup_events + 1):
        for t in e.get("transitions", []):
            if board.get(t["card"]) != by_name.get(t["from"]):
                problems.append(f"events: event {n} ({e['event']}) says {t['card']} went from {t['from']}, "
                                f"but the log had it at {operations.STATE_NAMES.get(board.get(t['card']))}")
            board[t["card"]] = by_name.get(t["to"])
    final = {c["id"]: c.get("color") for c in canvas.cards(canvas.load(vault)) if "id" in c}
    for card_id in sorted(set(board) | set(final)):
        if board.get(card_id) != final.get(card_id):
            problems.append(f"events: {card_id}: the transitions leave it {operations.STATE_NAMES.get(board.get(card_id))}, "
                            f"project.canvas has {operations.STATE_NAMES.get(final.get(card_id))}")

    # Contested cards: one non-takeover assignment per winner (a loser records nothing).
    for card_id in contested:
        won = [e for e in ours if e["event"] == "assigned" and e["card"] == card_id]
        fresh = [e for e in won if not e["data"]["takeover"]]
        if len(fresh) != len(wins.get(card_id, [])) or len(won) != len(fresh):
            problems.append(f"events: {card_id}: {len(fresh)} assignments without takeover ({len(won) - len(fresh)} "
                            f"with), but {len(wins.get(card_id, []))} winners")
    return problems


def check_critical(vault: Path) -> list[str]:
    """The critical path against the final board: what Histos painted is the path, minus the arrows
    somebody else colored, and the ledger says the same; and repainting left no event."""
    problems = []
    data = canvas.load(vault)
    cfg = config.load(vault)
    path = critical.current_path(vault, data)
    if cfg is None or path is None or len(path.cards) < 2:
        return ["critical: the stress vault has no critical path to check (config missing/off, or no dependencies)"]
    color = cfg.critical_path.color.upper()
    edges = {e["id"]: e for e in data["edges"]}
    by_pair = {(e["fromNode"], e["toNode"]): e["id"] for e in edges.values()}
    on_path = {by_pair[pair] for pair in zip(path.cards, path.cards[1:]) if pair in by_pair}
    ours = lambda e: (e.get("color") or "").upper() == color  # noqa: E731
    expected = {eid for eid in on_path if not edges[eid].get("color") or ours(edges[eid])}
    painted = {eid for eid, e in edges.items() if ours(e)}
    if painted != expected:
        problems.append(f"critical: arrows with the Histos color {sorted(painted)}, but the path "
                        f"{path.cards} calls for {sorted(expected)}")
    ledger = critical._read_ledger(vault)
    if ledger is None:
        problems.append("critical: .histos/critical-path.json is missing or unreadable")
    else:
        for eid in sorted(set(ledger) - set(edges)):
            problems.append(f"critical: the ledger notes {eid}, which is not on the canvas")
        for eid in sorted(set(ledger) & set(edges)):
            if not ours(edges[eid]):
                problems.append(f"critical: the ledger notes {eid}, but its color is {edges[eid].get('color')!r}")
        for eid in sorted(painted - set(ledger)):
            problems.append(f"critical: {eid} has the Histos color but is not in the ledger")
    unknown = sorted({e["event"] for e in events.read(vault)} - events.EVENTS)
    if unknown:
        problems.append(f"critical: events of types outside events.EVENTS (a repaint recorded?): {unknown}")
    return problems


def check(vault: Path, owned: dict[str, list[str]], contested: list[str], expected: dict[str, Expected],
          wins: dict[str, list[str]], report: Report, setup_events: int = 0,
          initial: dict[str, str] | None = None) -> list[str]:
    problems = []
    validate = histos(vault, "validate")
    if validate.returncode != 0:
        problems.append(f"validate failed:\n{validate.stdout}{validate.stderr}")
    try:
        data = canvas.load(vault)
    except (ValueError, canvas.HistosError) as e:
        return problems + [f"project.canvas unreadable: {e}"]

    for name, cards in owned.items():
        for card_id in cards:
            card = canvas.find_card(data, card_id)
            exp = expected[card_id]
            meta, _ = frontmatter.read(vault / "content" / f"{card_id}.md")
            if card["color"] != exp.color:
                problems.append(f"{card_id}: state {card['color']}, its owner {name} left {exp.color} (lost update)")
            if exp.description is not None and meta.get("description") != exp.description:
                problems.append(f"{card_id}: description {meta.get('description')!r}, expected {exp.description!r}")
            if meta.get("proposed_model") != exp.proposed_model:
                problems.append(f"{card_id}: proposed_model {meta.get('proposed_model')!r}, expected {exp.proposed_model!r}")
            if card["color"] == canvas.IN_PROGRESS and meta.get("worker") != name:
                problems.append(f"{card_id}: worker {meta.get('worker')!r}, expected {name}")

    for card_id in contested:
        winners = wins.get(card_id, [])
        if len(winners) > 1:
            problems.append(f"{card_id}: assigned {len(winners)} times while In progress: {winners}")
        meta, _ = frontmatter.read(vault / "content" / f"{card_id}.md")
        if winners and meta.get("worker") != winners[0]:
            problems.append(f"{card_id}: worker {meta.get('worker')!r}, but {winners[0]} won it")

    problems += check_events(vault, report, setup_events, initial or {}, contested, wins)
    problems += check_critical(vault)

    leftovers = [str(p.relative_to(vault)) for p in vault.rglob("*.tmp")]
    if leftovers:
        problems.append(f"temp files left: {leftovers}")
    lock_file = vault / ".histos.lock"
    if lock_file.exists() and lock_file.read_bytes():
        problems.append(f"lock file not empty: {lock_file.read_text(encoding='utf-8')!r}")
    problems += [f"crash: {c}" for c in report.crashes]
    return problems


def run(workers: int, rounds: int, seed: int, keep: bool = False, quiet: bool = False) -> list[str]:
    root = Path(tempfile.mkdtemp(prefix="histos-stress-"))
    previous_home = os.environ.get(events.HOME_ENV)
    os.environ[events.HOME_ENV] = str(root / "histos-home")  # subprocesses inherit it: never the real ~/.histos
    try:
        vault, owned, contested = make_vault(root, workers)
        setup_events = len(events.read(vault))
        initial = {c["id"]: c.get("color") for c in canvas.cards(canvas.load(vault)) if "id" in c}
        drafts = root / "drafts"
        drafts.mkdir()
        expected = {c: Expected(color=initial[c]) for cards in owned.values() for c in cards}
        wins: dict[str, list[str]] = {}
        report = Report()
        start = time.monotonic()
        threads = [
            threading.Thread(target=worker, args=(vault, name, cards, contested, rounds, seed,
                                                  expected, wins, report, drafts))
            for name, cards in owned.items()
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.monotonic() - start
        problems = check(vault, owned, contested, expected, wins, report, setup_events, initial)
        if not quiet:
            print(f"{workers} workers x {rounds} rounds, {sum(report.ops.values())} commands in {elapsed:.1f} s")
            for op in sorted(report.ops):
                print(f"  {op:9s} {report.ops[op]:4d}  ok {report.ok[op]:4d}  refused {report.refused[op]:4d}")
            print(f"  'vault is busy' timeouts: {report.busy}")
            print(f"  contested cards won: {sum(len(v) for v in wins.values())} of {len(contested)}")
            print("invariants: OK" if not problems else "invariants: FAILED\n  " + "\n  ".join(problems))
            if keep:
                print(f"vault kept at {vault}")
        return problems
    finally:
        if previous_home is None:
            os.environ.pop(events.HOME_ENV, None)
        else:
            os.environ[events.HOME_ENV] = previous_home
        if not keep:
            shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--keep", action="store_true", help="keep the temp vault to inspect it")
    args = parser.parse_args()
    return 1 if run(args.workers, args.rounds, args.seed, args.keep) else 0


if __name__ == "__main__":
    raise SystemExit(main())
