"""`histos estimate --coverage` (cod-coverage): how many closed cards each estimate group has.

The history is synthetic -- two vaults whose event logs are written by hand, registered in the
test's HISTOS_HOME -- so every figure is known beforehand."""
import hashlib
import json
from datetime import timedelta

import pytest
from conftest import run
from test_estimates_status import START, iso, register, set_meta
from test_history import A, B, P, R, ev, write_log

from histos import estimates, events, history, operations


def closed(n, card, tier, deps=(), agent_min=25, review_h=3, outside=False):
    """The events of one card that lived on its own day: created, classified, assigned, proposed, approved."""
    day = START + timedelta(days=n)
    proposed = day + timedelta(minutes=agent_min)
    approved = proposed + timedelta(hours=review_h)
    return [
        ev(iso(day - timedelta(hours=1)), "card_created", card, data={"title": card, "depends_on": list(deps)},
           transitions=[(card, None, B)]),
        ev(iso(day - timedelta(minutes=30)), "classified", card, data={"tier": tier}),
        ev(iso(day), "assigned", card, "parent", {"by": "agent", "takeover": False, "previous_worker": None},
           [(card, B, P)]),
        ev(iso(proposed), "state_recomputed" if outside else "proposed", card, "parent",
           {"seen_outside_histos": True} if outside else {"attempt": 1, "words": 10, "lines": 2},
           [(card, P, R)]),
        ev(iso(approved), "approved", card, None, {"words": 10, "lines": 2}, [(card, R, A)]),
    ]


@pytest.fixture
def world(tmp_path_factory, monkeypatch):
    """Vault A (typed, 11 cards), vault B (no prefixes, 2 exec cards), a registered folder with no
    log, and a real vault with three open `cod-` cards."""
    a = tmp_path_factory.mktemp("cov-a")
    log, n = [], 0
    for i in range(5):  # exec · cod · 0 deps
        log += closed(n, f"cod-a{i}", "exec"); n += 1
    for i in (5, 6):  # exec · cod · 1 dep (one of them hand-moved: approximate)
        log += closed(n, f"cod-a{i}", "exec", deps=["cod-a0"], outside=i == 6); n += 1
    for i in range(3):  # reasoning · dec; the last one sat In progress for 200 hours
        log += closed(n, f"dec-a{i}", "reasoning", agent_min=200 * 60 if i == 2 else 25); n += 1
    log += closed(n, "rel-a0", "fast")
    write_log(a, log, vault_id="a")
    register(a, "a")

    b = tmp_path_factory.mktemp("cov-b")
    write_log(b, closed(20, "alpha", "exec") + closed(21, "beta", "exec"), vault_id="b")
    register(b, "b")

    gone = tmp_path_factory.mktemp("cov-gone")
    register(gone, "gone")

    live = tmp_path_factory.mktemp("cov-live")
    run(monkeypatch, live, "init")
    for card in ("cod-open1", "cod-open2", "cod-open3"):
        run(monkeypatch, live, "add-card", card, "--title", card)
    for card in ("cod-open1", "cod-open2"):  # classified: they get an agent estimate
        set_meta(live, card, proposed_tier="exec")
    return {"a": a, "b": b, "gone": gone, "live": live}


def coverage(monkeypatch, tmp_path, capsys):
    capsys.readouterr()
    code = run(monkeypatch, tmp_path, "estimate", "--coverage")
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_the_three_blocks_with_exact_figures(world, monkeypatch, tmp_path, capsys):
    code, out, _ = coverage(monkeypatch, tmp_path, capsys)
    assert code == 0
    assert out == f"""History: 13 closed cards in 3 vaults (1 approximate) -- 1 vault skipped
  skipped: {world['gone']}: no event log there

Agent time                          cards   enough?
  fast · rel · 0 deps                   1   4 more
  fast · rel                            1   4 more
  fast                                  1   4 more
  exec · cod · 0 deps                   5   yes
  exec · cod · 1 dep                    2   3 more
  exec · cod                            7   yes
  exec                                  9   yes
  reasoning · dec · 0 deps              3   2 more
  reasoning · dec                       3   2 more
  reasoning                             3   2 more

Review time                         cards   enough?
  cod                                   7   yes
  dec                                   3   2 more
  rel                                   1   4 more
  all cards                            13   yes

Open cards with an estimate right now: agent 2 of 3, review 3 of 3

Agent time over 7 days (forgotten In progress?)
  {world['a'].name} · dec-a2: 8.3 days
"""


def test_five_samples_are_enough_and_four_are_not(world, monkeypatch, tmp_path, capsys):
    _, out, _ = coverage(monkeypatch, tmp_path, capsys)
    assert "exec · cod · 0 deps                   5   yes" in out
    assert "yes" not in next(line for line in out.splitlines() if "reasoning · dec ·" in line)
    assert estimates.still_needed(4) == 1 and estimates.still_needed(5) == 0 and estimates.still_needed(9) == 0


def test_it_uses_the_same_groups_the_estimates_do(world, monkeypatch, tmp_path, capsys):
    """A group listed as "yes" is exactly one for_card would estimate from."""
    found, ids = history.read_all()
    rows = estimates.typed(found, ids)
    for group, count in estimates.agent_coverage(rows):
        card = estimates.for_card(rows, group[0], group[1] if group[1] != estimates.ANY else None,
                                  group[2] if group[2] != estimates.ANY else 0)
        if group[1] != estimates.ANY and group[2] != estimates.ANY and count >= estimates.MIN_SAMPLES:
            assert isinstance(card.agent, estimates.Estimate)


def test_reading_writes_nothing(world, monkeypatch, tmp_path, capsys):
    def snapshot():
        files = {}
        for root in (*world.values(), events.home()):
            for p in sorted(root.rglob("*")):
                if p.is_file():
                    files[str(p)] = (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
        return files

    before = snapshot()
    code, _, _ = coverage(monkeypatch, tmp_path, capsys)
    assert code == 0
    assert snapshot() == before  # no vault, no registry, no log, no card file changed -- and no lock left


def test_no_history_says_so(tmp_path, monkeypatch, capsys):
    code, out, _ = coverage(monkeypatch, tmp_path, capsys)
    assert code == 0
    assert out == "no closed cards yet: estimates start after the first approvals with histos 3.1+\n"


def test_without_the_flag_it_explains(tmp_path, monkeypatch, capsys):
    capsys.readouterr()
    assert run(monkeypatch, tmp_path, "estimate") == 1
    err = capsys.readouterr().err
    assert "--coverage" in err and "histos status" in err


def test_the_open_card_count_spans_every_registered_vault(world, monkeypatch, tmp_path, capsys):
    report = operations.coverage()
    assert (report.open_cards, report.open_with_agent, report.open_with_review) == (3, 2, 3)
