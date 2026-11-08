"""The shortcuts that keep estimates fast with thousands of closed cards give the same answers as the slow way.

Found by tests/test_estimates_scale.py: `status` took ~18 s and `--coverage` ~6 s with 2 000 closed
cards, because every open card re-walked every closed card, every review span re-walked every
pause, and `status` read the whole history twice."""
import random
from datetime import datetime, timedelta, timezone

from test_estimates import T0, rows, sample
from test_history import A, B, P, R, approve, assign, at, created, propose, write_log

from histos import estimates, history
from histos.estimates import ANY, Row, Rows


def naive_active_hours(spans, away):
    """The original definition: subtract every overlap with every pause."""
    total = 0.0
    for start, end in spans:
        seconds = max(0.0, (end - start).total_seconds())
        for a_start, a_end in away:
            overlap = (min(end, a_end) - max(start, a_start)).total_seconds()
            if overlap > 0:
                seconds -= overlap
        total += max(0.0, seconds)
    return total / 3600


def test_pauses_found_by_bisection_equal_walking_all_of_them():
    rng = random.Random(4)
    day0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    away = [(day0 + timedelta(days=d, hours=22), day0 + timedelta(days=d + 1, hours=rng.randint(6, 9)))
            for d in range(0, 300, 2)]
    for _ in range(400):
        start = day0 + timedelta(minutes=rng.randint(0, 300 * 1440))
        spans = [(start, start + timedelta(minutes=rng.choice([0, 5, 90, 600, 1500, 5000])))]
        assert history._active_hours(spans, away) == naive_active_hours(spans, away)
    spans = [(away[3][0] - timedelta(hours=1), away[3][1] + timedelta(hours=1))]  # one that swallows a pause
    assert history._active_hours(spans, away) == naive_active_hours(spans, away)
    assert history._active_hours(spans, []) == (spans[0][1] - spans[0][0]).total_seconds() / 3600


def test_indexed_rows_estimate_like_a_plain_list():
    data = rows(40, deps=1, agent=2.0) + rows(7, deps=0, agent=5.0) + rows(3, type_="dec", tier="fast", agent=1.0)
    for n, row in enumerate(data):  # distinct values and approval dates, so the 30 latest matter
        data[n] = Row(sample(n=n, card=f"x{n}", tier=row.sample.tier, deps=row.sample.dependencies,
                             agent=1.0 + n % 9, review=2.0 + n % 5), row.type)
    indexed = Rows(data)
    for groups in (estimates.agent_groups("exec", "cod", 1), estimates.agent_groups("exec", "cod", 0),
                   estimates.agent_groups("fast", "dec", 0), estimates.agent_groups("reasoning", "cod", 0),
                   estimates.review_groups("cod"), estimates.review_groups(None), [(ANY, ANY, ANY)]):
        for value in (estimates.agent_value, estimates.review_value):
            assert estimates.estimate(indexed, groups, value) == estimates.estimate(list(data), groups, value)
    assert estimates.agent_coverage(indexed) == estimates.agent_coverage(list(data))
    assert estimates.review_coverage(indexed) == estimates.review_coverage(list(data))
    assert [r.sample.card for r in indexed.members(("exec", "cod", 1))] == [
        r.sample.card for r in sorted(data, key=lambda r: r.sample.approved_at) if r.key == ("exec", "cod", 1)]
    assert indexed.members(("exec", "cod", 1)) is indexed.members(("exec", "cod", 1))  # worked out once
    assert isinstance(estimates.typed([], {}), Rows)


def two_cards(vault):
    write_log(vault, [created(at("09:00"), "a"), assign(at("10:00"), "a"), propose(at("11:00"), "a"),
                      approve(at("12:00"), "a")], vault_id="snap")


def register(vault):
    from test_estimates_status import register as add
    add(vault, "snap")


def test_a_snapshot_reads_the_logs_once(tmp_path, monkeypatch):
    two_cards(tmp_path)
    register(tmp_path)
    reads = []
    real = history._replay
    monkeypatch.setattr(history, "_replay", lambda vault: reads.append(vault) or real(vault))

    history.read_all()
    history.read_all()
    assert len(reads) == 2  # outside a snapshot every call reads

    reads.clear()
    with history.snapshot():
        first, ids = history.read_all()
        again, ids_again = history.read_all()
        with history.snapshot():  # a nested one joins the outer snapshot
            history.read_all()
    assert len(reads) == 1 and again == first and ids_again == ids
    assert again is not first and ids_again is not ids  # callers can't change each other's lists

    history.read_all()
    assert len(reads) == 2  # the snapshot is over: logs are read afresh


def test_a_snapshot_keeps_the_skipped_vaults_for_every_caller(tmp_path):
    from test_estimates_status import register as add
    two_cards(tmp_path)
    add(tmp_path, "snap")
    gone = tmp_path / "gone"
    gone.mkdir()
    add(gone, "gone")
    with history.snapshot():
        first, second = [], []
        history.read_all(None, first)
        history.read_all(None, second)
    assert first == second == [f"{gone}: no event log there"]
