"""Stress test: several agents (parent and subagents) hammering one vault at the same time.

Works on a throwaway vault in a temp folder -- never on a real one. N workers run R rounds
each; every operation is a separate `histos` process, as with real agents, so processes race
for project.canvas and the vault lock. Then it checks the invariants:

- project.canvas is valid JSON and `histos validate` passes (frontmatter included);
- no lost update: each worker owns some cards, and every card ends exactly as its owner's last
  successful operation left it (state, description, proposed model);
- contested cards (every worker tries to `assign` them) end with exactly one winner;
- no *.tmp left in the vault, the lock file is empty, and no command crashed (traceback).

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
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from histos import canvas, frontmatter  # noqa: E402

CARDS_PER_WORKER = 4
CONTESTED = 3
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
    for card_id in [c for cards in owned.values() for c in cards] + contested:
        assert histos(vault, "add-card", card_id, "--title", card_id).returncode == 0
    return vault, owned, contested


def worker(vault: Path, name: str, cards: list[str], contested: list[str], rounds: int, seed: int,
           expected: dict[str, Expected], wins: dict[str, list[str]], report: Report, drafts: Path) -> None:
    rng = random.Random(f"{seed}-{name}")
    good, bad = drafts / f"{name}-good.md", drafts / f"{name}-bad.md"
    good.write_text(GOOD_DRAFT, encoding="utf-8")
    bad.write_text(BAD_DRAFT, encoding="utf-8")

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
        if exp.color != canvas.PROPUESTA_PENDIENTE:
            possible += ["describe", "model", "classify"]
        if exp.color == canvas.EN_PROGRESO:
            possible += ["propose", "propose"]
        op = rng.choice(possible)
        if op == "assign" and exp.color == canvas.BACKLOG:
            if call("assign", "assign", card, "--worker", name).returncode == 0:
                exp.color = canvas.EN_PROGRESO
        elif op == "describe" and exp.color != canvas.PROPUESTA_PENDIENTE:
            text = f"{name} round {round_no}"
            if call("describe", "describe", card, "--text", text).returncode == 0:
                exp.description = text
        elif op == "model" and exp.color != canvas.PROPUESTA_PENDIENTE:
            model = f"model-{name}-{round_no}"
            if call("model", "model", card, "--proposed", model).returncode == 0:
                exp.proposed_model = model
        elif op == "classify" and exp.color != canvas.PROPUESTA_PENDIENTE:
            result = call("classify", "classify", card)
            if result.returncode == 0 and "tier changed" in result.stderr:
                exp.proposed_model = None  # a tier change legitimately clears the models
        elif op == "propose" and exp.color == canvas.EN_PROGRESO:
            draft = bad if rng.random() < 0.4 else good
            result = call("propose", "propose", card, "--file", str(draft))
            if result.returncode == 0:
                exp.color = canvas.PROPUESTA_PENDIENTE
            elif "not proposed" in result.stderr:
                exp.failures += 1
        elif op == "read":
            call("context", "context", card, "--for-subagent") if exp.color == canvas.EN_PROGRESO \
                else call("status", "status")
        elif op == "contest":
            target = rng.choice(contested)
            result = call("assign", "assign", target, "--worker", name)
            if result.returncode == 0 and "already yours" not in result.stdout:  # re-assigning your own is a no-op
                with report.lock:
                    wins.setdefault(target, []).append(name)


def check(vault: Path, owned: dict[str, list[str]], contested: list[str], expected: dict[str, Expected],
          wins: dict[str, list[str]], report: Report) -> list[str]:
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
            if card["color"] == canvas.EN_PROGRESO and meta.get("worker") != name:
                problems.append(f"{card_id}: worker {meta.get('worker')!r}, expected {name}")

    for card_id in contested:
        winners = wins.get(card_id, [])
        if len(winners) > 1:
            problems.append(f"{card_id}: assigned {len(winners)} times while In progress: {winners}")
        meta, _ = frontmatter.read(vault / "content" / f"{card_id}.md")
        if winners and meta.get("worker") != winners[0]:
            problems.append(f"{card_id}: worker {meta.get('worker')!r}, but {winners[0]} won it")

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
    try:
        vault, owned, contested = make_vault(root, workers)
        drafts = root / "drafts"
        drafts.mkdir()
        expected = {c: Expected() for cards in owned.values() for c in cards}
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
        problems = check(vault, owned, contested, expected, wins, report)
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
