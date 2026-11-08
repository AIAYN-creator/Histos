"""Estimates and `--coverage` at scale, end to end (pru-estimador).

The histories come from scripts/make_history.py: vaults whose event logs have the shape the CLI
writes and whose timings are drawn from recipes with a fixed seed. The generator also returns what
every card was MEANT to take, so each expected figure here is computed from that plan with plain
`statistics` and sorting -- never with the code under test -- and compared with what `histos
status` (and the `estimate` stored in each card) and `histos estimate --coverage` say."""
import importlib.util
import json
import math
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest
from conftest import run

from histos import events, frontmatter, history

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "make_history.py"
_spec = importlib.util.spec_from_file_location("make_history", SCRIPT)
mh = importlib.util.module_from_spec(_spec)
sys.modules["make_history"] = mh  # dataclasses look their module up here
_spec.loader.exec_module(mh)

MIN_SAMPLES, RECENT = 5, 30


# --- The statistics, written out independently of estimates.py -----------------------------------

def p80(values):
    """Nearest rank: the smallest value with at least 80% of the data at or below it."""
    ordered = sorted(values)
    return next(x for x in ordered if sum(y <= x for y in ordered) >= 0.8 * len(ordered))


def stats_of(truths, value):
    recent = sorted(truths, key=lambda t: t.approved_at)[-RECENT:]
    numbers = [value(t) for t in recent]
    return statistics.median(numbers), p80(numbers)


def agent_ladder(tier, type_, deps):
    """[(label of the group, the truths in it)] from the most specific group to the broadest."""
    bucket = min(deps, 2)
    rungs = []
    if type_:
        rungs.append(lambda t: (t.tier, t.type, min(t.deps, 2)) == (tier, type_, bucket))
        rungs.append(lambda t: (t.tier, t.type) == (tier, type_))
    rungs.append(lambda t: t.tier == tier)
    return rungs


def review_ladder(type_):
    return ([lambda t: t.type == type_] if type_ else []) + [lambda t: True]


def expected(truths, rungs, value):
    """(median, p80, members) from the first group with MIN_SAMPLES cards, or None."""
    for rung in rungs:
        members = [t for t in truths if rung(t)]
        if len(members) >= MIN_SAMPLES:
            return (*stats_of(members, value), len(members))
    return None


def agent_h(t):
    return t.agent_hours


def review_h(t):
    return t.review_active_hours


def agent_expected(info, o):
    return expected(info.truths, agent_ladder(o.tier, o.type, o.deps), agent_h) if o.tier else None


def review_expected(info, o):
    return expected(info.truths, review_ladder(o.type), review_h)


# --- Running things -------------------------------------------------------------------------------

def point_at(info, monkeypatch):
    monkeypatch.setenv(events.HOME_ENV, str(info.home))


def stored(info, o):
    meta, _ = frontmatter.read(info.vault_of(o.vault) / "content" / f"{o.card}.md")
    return meta.get("estimate") or {}


def status_all(info, monkeypatch, capsys):
    """`histos status` in every vault; returns {vault name: its output}."""
    point_at(info, monkeypatch)
    out = {}
    for name in info.names:
        capsys.readouterr()
        assert run(monkeypatch, info.vault_of(name), "status") == 0
        out[name] = capsys.readouterr().out
    return out


def check_estimate(part, want):
    """A stored `agent_hours` / `review_hours` against the independent figures."""
    if want is None:
        assert part is None
    else:
        median, high, _ = want
        assert part["median"] == round(median, 2) and part["p80"] == round(high, 2)


def hours_text(h):
    from histos.estimates import format_hours
    return format_hours(h)


def build(tmp_path, name="h", seed=1):
    return mh.History(tmp_path / name, seed)


def plain_batch(w, count, **kw):
    return [w.plain(**kw) for _ in range(count)]


# 0a. The generator is faithful: what history.py reads back is what it planned
def test_the_generator_matches_the_replay_of_its_own_logs(tmp_path, monkeypatch):
    info = mh.generate(tmp_path / "gen", cards=150, vaults=3, seed=11)
    point_at(info, monkeypatch)
    by_key = {(s.vault, s.card): s for s in history.samples()}
    assert len(by_key) == len(info.truths) == 150
    ids = {p.name: json.loads((p / ".histos" / "vault.json").read_text())["id"] for p in info.vaults}
    for t in info.truths:
        s = by_key[(ids[t.vault], t.card)]
        assert (s.agent_hours, s.review_hours, s.review_active_hours) == (
            t.agent_hours, t.review_hours, t.review_active_hours), t
        assert (s.tier, s.dependencies, s.rejections) == (t.tier, t.deps, t.rejections)
    kinds = {t.kind for t in info.truths}
    assert kinds == {"plain", "night", "rejected", "exhausted", "afk", "subagent"}
    assert any(t.review_hours > t.review_active_hours for t in info.truths)  # the pauses bite
    for vault in info.vaults:
        assert events.check_log(vault) == ([], [])  # every line fits histos-events.schema.json


# 0b. The generator only writes where it is safe to
def test_it_refuses_a_folder_that_is_not_new_or_empty(tmp_path, capsys):
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "precious.txt").write_text("keep me", encoding="utf-8")
    assert mh.main([str(busy)]) == 2
    assert "not empty" in capsys.readouterr().err
    assert [p.name for p in busy.iterdir()] == ["precious.txt"]
    (tmp_path / "a-file").write_text("x", encoding="utf-8")
    with pytest.raises(mh.UnsafeOutput):
        mh.check_output(tmp_path / "a-file")


def test_it_refuses_the_users_home_and_histos_home(tmp_path, monkeypatch):
    fake_home = tmp_path / "fake-home"
    (fake_home / ".histos").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    for unsafe in (fake_home, fake_home / ".histos", fake_home / ".histos" / "inside", tmp_path):
        with pytest.raises(mh.UnsafeOutput):
            mh.check_output(unsafe)
    elsewhere = tmp_path / "elsewhere"
    monkeypatch.setenv(events.HOME_ENV, str(elsewhere))
    with pytest.raises(mh.UnsafeOutput):
        mh.check_output(elsewhere)
    assert mh.check_output(tmp_path / "fresh" / "nested") == (tmp_path / "fresh" / "nested").resolve()


def test_it_is_reproducible(tmp_path):
    a = mh.generate(tmp_path / "one", cards=60, vaults=2, seed=5)
    b = mh.generate(tmp_path / "two", cards=60, vaults=2, seed=5)
    c = mh.generate(tmp_path / "three", cards=60, vaults=2, seed=6)
    shape = lambda i: [(t.vault, t.card, t.tier, t.deps, t.agent_hours, t.review_hours) for t in i.truths]  # noqa: E731
    assert shape(a) == shape(b) != shape(c)


# 1. Exact statistics
def test_status_shows_the_exact_median_and_percentile(tmp_path, monkeypatch, capsys):
    info = mh.generate(tmp_path / "gen", cards=200, vaults=3, seed=7)
    outputs = status_all(info, monkeypatch, capsys)
    seen_agent = 0
    for o in info.opens:
        est = stored(info, o)
        want_agent, want_review = agent_expected(info, o), review_expected(info, o)
        check_estimate(est.get("agent_hours"), want_agent)
        check_estimate(est.get("review_hours"), want_review)
        line = next(x for x in outputs[o.vault].splitlines() if x.strip().startswith(f"- {o.card} "))
        if want_agent:
            seen_agent += 1
            assert f"agent {hours_text(want_agent[0])} (≤{hours_text(want_agent[1])[1:]})" in line
        elif o.tier:
            assert "agent no data" in line
        assert f"review {hours_text(want_review[0])} (≤{hours_text(want_review[1])[1:]})" in line
    assert seen_agent >= 10  # plenty of cards with a figure were checked, and the ones without say so


def test_a_group_of_forty_uses_its_thirty_latest(tmp_path, monkeypatch, capsys):
    h = build(tmp_path)
    w = h.vault("vault-a")
    cards = [w.plain(latent="cod", tier="exec", deps=0, agent_s=(10 + n) * 60, review_s=(60 + 3 * n) * 60)
             for n in range(40)]  # each slower than the last
    card = w.open_card(mh.B, latent="cod", tier="exec", deps=0)
    info = h.finish(idle_nights=0)
    status_all(info, monkeypatch, capsys)
    est = stored(info, next(o for o in info.opens if o.card == card))
    latest = cards[-RECENT:]
    assert (est["agent_hours"]["median"], est["agent_hours"]["p80"]) == tuple(
        round(x, 2) for x in (statistics.median(t.agent_hours for t in latest), p80([t.agent_hours for t in latest])))
    assert est["agent_hours"]["median"] != round(statistics.median(t.agent_hours for t in cards), 2)
    assert est["agent_hours"]["basis"].endswith("40 cards")


# 2. The group ladder: 4 samples fall to the next group, 5 use their own
def ladder_history(tmp_path, name, specific, rel):
    h = build(tmp_path, name)
    w = h.vault("vault-a")
    plain_batch(w, 6, latent="cod", tier="exec", deps=0)
    plain_batch(w, specific, latent="cod", tier="exec", deps=1)
    plain_batch(w, 3, latent="dec", tier="exec", deps=0)
    plain_batch(w, 4, latent="cod", tier="reasoning", deps=0)
    plain_batch(w, rel, latent="rel", tier="fast", deps=0)
    asks = {
        "specific": w.open_card(mh.B, latent="cod", tier="exec", deps=1),
        "tier_only": w.open_card(mh.B, latent="dec", tier="exec", deps=0),
        "missing": w.open_card(mh.B, latent="cod", tier="reasoning", deps=0),
        "rel": w.open_card(mh.B, latent="rel", tier="fast", deps=0),
    }
    return h.finish(idle_nights=0), asks


def test_the_ladder_at_four_samples(tmp_path, monkeypatch, capsys):
    info, asks = ladder_history(tmp_path, "four", specific=4, rel=4)
    status_all(info, monkeypatch, capsys)
    exec_cod = len([t for t in info.truths if (t.tier, t.type) == ("exec", "cod")])
    exec_all = len([t for t in info.truths if t.tier == "exec"])
    get = lambda key: stored(info, next(o for o in info.opens if o.card == asks[key]))  # noqa: E731
    assert get("specific")["agent_hours"]["basis"] == f"exec · cod · {exec_cod} cards"      # 4 deps=1: one rung down
    assert get("tier_only")["agent_hours"]["basis"] == f"exec · {exec_all} cards"           # 3 exec/dec: down to the tier
    assert get("missing")["agent_hours"] is None                                            # 4 reasoning cards in all
    assert get("rel")["review_hours"]["basis"] == f"all · {len(info.truths)} cards"         # 4 rel: down to everyone


def test_the_ladder_at_five_samples(tmp_path, monkeypatch, capsys):
    info, asks = ladder_history(tmp_path, "five", specific=5, rel=5)
    out = status_all(info, monkeypatch, capsys)["vault-a"]
    get = lambda key: stored(info, next(o for o in info.opens if o.card == asks[key]))  # noqa: E731
    assert re.fullmatch(r"exec · cod · 1 deps? · 5 cards", get("specific")["agent_hours"]["basis"])
    assert get("rel")["review_hours"]["basis"] == "rel · 5 cards"
    own = [t for t in info.truths if (t.tier, t.type, t.deps) == ("exec", "cod", 1)]
    assert get("specific")["agent_hours"]["median"] == round(stats_of(own, agent_h)[0], 2)
    # the card with 4 reasoning cards says how many it lacks, in status too
    line = next(x for x in out.splitlines() if x.strip().startswith(f"- {asks['missing']} "))
    assert "agent no data (1 more needed)" in line


# 3. Recency: only the 30 latest count
def test_old_slow_cards_do_not_weigh_once_thirty_fast_ones_exist(tmp_path, monkeypatch, capsys):
    h = build(tmp_path)
    w = h.vault("vault-a")
    old = plain_batch(w, 30, latent="cod", tier="exec", deps=0, agent_s=3 * 3600, review_s=20 * 3600)
    recent = plain_batch(w, 30, latent="cod", tier="exec", deps=0, agent_s=10 * 60, review_s=3600)
    card = w.open_card(mh.B, latent="cod", tier="exec", deps=0)
    info = h.finish(idle_nights=0)
    status_all(info, monkeypatch, capsys)
    est = stored(info, next(o for o in info.opens if o.card == card))
    assert (est["agent_hours"]["median"], est["agent_hours"]["p80"]) == (round(10 / 60, 2),) * 2
    assert est["agent_hours"]["basis"].endswith("60 cards")  # the group is 60 strong; the estimate, 30
    assert (est["review_hours"]["median"], est["review_hours"]["p80"]) == (1.0, 1.0)
    assert min(t.approved_at for t in recent) > max(t.approved_at for t in old)


# 4. Pauses: same history with and without the nights
def test_night_pauses_lower_the_review_estimate_by_exactly_the_nights(tmp_path, monkeypatch, capsys):
    h = build(tmp_path)
    w = h.vault("vault-a")
    nights = plain_batch(w, 12, night=True, latent="cod", tier="exec", deps=0)
    card = w.open_card(mh.B, latent="cod", tier="exec", deps=0)
    info = h.finish(idle_nights=0)
    assert len(info.pauses) == 12
    pauses_file = info.home / "pauses.jsonl"
    o = next(x for x in info.opens if x.card == card)

    status_all(info, monkeypatch, capsys)
    with_pauses = stored(info, o)
    pauses_file.rename(info.home / "pauses.off")
    status_all(info, monkeypatch, capsys)
    without = stored(info, o)

    assert with_pauses["agent_hours"] == without["agent_hours"]            # the agent never pauses
    calendar = stats_of(nights, lambda t: t.review_hours)
    active = stats_of(nights, lambda t: t.review_active_hours)
    assert (without["review_hours"]["median"], without["review_hours"]["p80"]) == tuple(round(x, 2) for x in calendar)
    assert (with_pauses["review_hours"]["median"], with_pauses["review_hours"]["p80"]) == tuple(round(x, 2) for x in active)
    assert with_pauses["review_hours"]["median"] < without["review_hours"]["median"]

    # the hours taken away are exactly the nights written in pauses.jsonl (read back here by hand)
    lines = [json.loads(x) for x in (info.home / "pauses.off").read_text(encoding="utf-8").splitlines()]
    when = lambda x: datetime.fromisoformat(x["ts"].replace("Z", "+00:00"))  # noqa: E731
    starts = [when(x) for x in lines if x["event"] == "pause"]
    ends = [when(x) for x in lines if x["event"] == "resume"]
    night_hours = sum((e - s).total_seconds() for s, e in zip(starts, ends)) / 3600
    point_at(info, monkeypatch)
    off = {s.card: s for s in history.samples()}  # pauses.off is not read: no pauses
    (info.home / "pauses.off").rename(pauses_file)
    on = {s.card: s for s in history.samples()}
    assert sum(off[c].review_active_hours - on[c].review_active_hours for c in on) == pytest.approx(night_hours)
    assert all(off[c].review_active_hours == off[c].review_hours for c in off)
    assert all(on[c].agent_hours == off[c].agent_hours for c in on)


# 5. An AFK batch is worked one card at a time
def afk_estimate(tmp_path, monkeypatch, capsys, name, one_by_one):
    h = build(tmp_path, name)
    w = h.vault("vault-a")
    truths = w.afk(5, one_by_one=one_by_one, agent_minutes=[10, 20, 30, 40, 50], review_hours=[3] * 5,
                   latent="cod", tier="exec", deps=0)
    card = w.open_card(mh.B, latent="cod", tier="exec", deps=0)
    info = h.finish(idle_nights=0)
    status_all(info, monkeypatch, capsys)
    return truths, stored(info, next(o for o in info.opens if o.card == card))


def test_five_cards_assigned_at_once_estimate_like_five_assigned_one_by_one(tmp_path, monkeypatch, capsys):
    batch_truths, batch = afk_estimate(tmp_path, monkeypatch, capsys, "batch", one_by_one=False)
    serial_truths, serial = afk_estimate(tmp_path, monkeypatch, capsys, "serial", one_by_one=True)
    assert [t.agent_hours for t in batch_truths] == [m / 60 for m in (10, 20, 30, 40, 50)]
    assert batch["agent_hours"] == serial["agent_hours"] and batch["review_hours"] == serial["review_hours"]
    assert (batch["agent_hours"]["median"], batch["agent_hours"]["p80"]) == (0.5, round(40 / 60, 2))
    # read as parallel work, the same batch would have taken 10, 30, 60, 100 and 150 minutes
    assert batch["agent_hours"]["median"] != 1.0


# 6. Types
def test_types_are_shared_between_vaults_but_not_with_a_vault_without_prefixes(tmp_path, monkeypatch, capsys):
    h = build(tmp_path)
    a, b = h.vault("vault-a"), h.vault("vault-b")
    c = h.vault("vault-c", prefixed=False)
    plain_batch(a, 3, latent="cod", tier="exec", deps=0)
    plain_batch(b, 3, latent="cod", tier="exec", deps=0)
    plain_batch(c, 6, latent="cod", tier="exec", deps=0)
    in_a = a.open_card(mh.B, latent="cod", tier="exec", deps=0)
    in_c = c.open_card(mh.B, latent="cod", tier="exec", deps=0)
    info = h.finish(idle_nights=0)
    status_all(info, monkeypatch, capsys)
    typed = lambda card: stored(info, next(o for o in info.opens if o.card == card))  # noqa: E731

    # 3 + 3 `cod` cards from two vaults make one group of 6; the untyped vault's cards are not in it
    assert typed(in_a)["agent_hours"]["basis"] == "exec · cod · 0 deps · 6 cards"
    assert typed(in_a)["review_hours"]["basis"] == "cod · 6 cards"
    # the vault without prefixes only has the tier group (12 exec cards) and "all cards" for review
    assert typed(in_c)["agent_hours"]["basis"] == "exec · 12 cards"
    assert typed(in_c)["review_hours"]["basis"] == "all · 12 cards"
    assert all(o.type is None for o in info.opens if o.vault == "vault-c")
    cod = [t for t in info.truths if t.type == "cod"]
    assert typed(in_a)["review_hours"]["median"] == round(stats_of(cod, review_h)[0], 2)


# 7. --coverage
def test_coverage_counts_and_footer_match_the_generated_data(tmp_path, monkeypatch, capsys):
    info = mh.generate(tmp_path / "gen", cards=200, vaults=3, seed=7)
    point_at(info, monkeypatch)
    capsys.readouterr()
    assert run(monkeypatch, info.vaults[0], "estimate", "--coverage") == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0] == f"History: {len(info.truths)} closed cards in 3 vaults (0 approximate)"

    def label(tier, type_, deps):
        parts = ([tier] if tier else []) + ([type_] if type_ else [])
        if deps is not None:
            parts.append(f"{deps}{'+' if deps == 2 else ''} dep{'' if deps == 1 else 's'}")
        return " · ".join(parts) or "all cards"

    want_agent, want_review = {}, {}
    for t in info.truths:
        for key in ([(t.tier, t.type, min(t.deps, 2)), (t.tier, t.type, None)] if t.type else []) + [(t.tier, None, None)]:
            want_agent[label(*key)] = want_agent.get(label(*key), 0) + 1
        for key in ([(None, t.type, None)] if t.type else []) + [(None, None, None)]:
            want_review[label(*key)] = want_review.get(label(*key), 0) + 1

    def table(title):
        start = next(i for i, x in enumerate(lines) if x.startswith(title)) + 1
        rows = {}
        for x in lines[start:]:
            if not x.strip():
                break
            m = re.fullmatch(r"  (.+?)\s+(\d+)\s+(yes|\d+ more)", x)
            assert m, x
            rows[m.group(1)] = (int(m.group(2)), m.group(3))
        return rows

    for title, want in (("Agent time", want_agent), ("Review time", want_review)):
        got = table(title)
        assert {k: v[0] for k, v in got.items()} == want
        for k, (count, enough) in got.items():
            assert enough == ("yes" if count >= MIN_SAMPLES else f"{MIN_SAMPLES - count} more"), k

    with_agent = sum(agent_expected(info, o) is not None for o in info.opens)
    with_review = sum(review_expected(info, o) is not None for o in info.opens)
    assert with_agent < len(info.opens)  # some open cards are unclassified or in thin groups
    assert (f"Open cards with an estimate right now: agent {with_agent} of {len(info.opens)}, "
            f"review {with_review} of {len(info.opens)}") in out


# 8. Performance
@pytest.mark.slow
def test_status_and_coverage_stay_under_two_seconds_with_two_thousand_cards(tmp_path, monkeypatch, capsys):
    info = mh.generate(tmp_path / "gen", cards=2000, vaults=5, seed=2)
    assert len(info.truths) == 2000 and len(info.vaults) == 5
    point_at(info, monkeypatch)
    vault = info.vaults[0]
    # A vault that has been used has had its files opened before; files written a moment ago are
    # still being scanned by the OS (antivirus on Windows: ~15 ms per file, 11 s for this vault).
    # That is the machine, not Histos, so read everything once before timing.
    for path in info.out.rglob("*"):
        if path.is_file():
            path.read_bytes()

    def seconds(*argv):
        """The best of three runs: the bound is on the work, not on whatever else the machine is doing."""
        best = None
        for _ in range(3):
            capsys.readouterr()
            began = time.perf_counter()
            assert run(monkeypatch, vault, *argv) == 0
            took = time.perf_counter() - began
            best = took if best is None else min(best, took)
        return best

    status_seconds = seconds("status")
    coverage_seconds = seconds("estimate", "--coverage")
    out = capsys.readouterr().out
    print(f"\n2000 cards, 5 vaults: status {status_seconds:.2f} s, --coverage {coverage_seconds:.2f} s")
    assert "History: 2000 closed cards in 5 vaults" in out
    assert status_seconds < 2.0 and coverage_seconds < 2.0
