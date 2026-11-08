"""approve fills actual_duration_hours: agent time + the human's active review time (cod-estimador).

The vault is real (made with the CLI up to the proposal) but its event log is replaced by a
hand-written one with round hours, and the moment of approval is fixed, so the figure is exact."""
from datetime import datetime, timezone

import pytest
from conftest import run
from test_history import assign, at, created, propose, write_log, write_pauses

from histos import events, frontmatter, operations


@pytest.fixture
def in_review(initialized_vault, monkeypatch, tmp_path):
    v = initialized_vault
    draft = tmp_path.parent / f"{tmp_path.name}-draft.md"
    draft.write_text("# A\n\nA real draft with enough words.\n", encoding="utf-8")
    run(monkeypatch, v, "add-card", "a", "--title", "A")
    run(monkeypatch, v, "assign", "a", "--worker", "parent")
    run(monkeypatch, v, "propose", "a", "--file", str(draft))
    return v


def approve_at(monkeypatch, vault, moment):
    monkeypatch.setattr(operations, "_now", lambda: moment)
    run(monkeypatch, vault, "approve", "a")
    meta, _ = frontmatter.read(vault / "content" / "a.md")
    return meta["actual_duration_hours"]


def test_actual_is_agent_time_plus_active_review_time(in_review, monkeypatch):
    # agent 10:00-10:30 (0.5 h); review 10:30-16:30 = 6 h on the calendar, of which the human
    # was away 12:30-14:30 (a closed pause) and from 15:30 on (still open when they approve)
    write_log(in_review, [created(at("09:00"), "a"), assign(at("10:00"), "a"), propose(at("10:30"), "a")])
    write_pauses((at("12:30"), at("14:30")), (at("15:30"), None))
    now = datetime(2026, 12, 1, 16, 30, tzinfo=timezone.utc)
    assert approve_at(monkeypatch, in_review, now) == 3.5  # 0.5 + (6 - 2 - 1), not 0.5 + 6


def test_without_pauses_the_whole_wait_counts(in_review, monkeypatch):
    write_log(in_review, [created(at("09:00"), "a"), assign(at("10:00"), "a"), propose(at("10:20"), "a")])
    now = datetime(2026, 12, 1, 12, 20, tzinfo=timezone.utc)
    assert approve_at(monkeypatch, in_review, now) == 2.33  # 20 min + 2 h, rounded to 2 decimals


def test_it_matches_what_history_measures_afterwards(in_review, monkeypatch):
    """The number written at approval is the one history.py gets once the approved event is logged."""
    from histos import history

    write_log(in_review, [created(at("09:00"), "a"), assign(at("10:00"), "a"), propose(at("10:30"), "a")])
    write_pauses((at("11:00"), at("12:00")))
    moment = datetime(2026, 12, 1, 14, 30, tzinfo=timezone.utc)
    actual = approve_at(monkeypatch, in_review, moment)
    log = events.read(in_review)
    # replace the real approval time by the fixed one, as if it had been logged at that moment
    for e in log:
        if e["event"] == "approved":
            e["ts"] = "2026-12-01T14:30:00.000Z"
    write_log(in_review, log)
    [sample] = history.vault_samples(in_review)
    assert actual == round(sample.agent_hours + sample.review_active_hours, 2) == 3.5


def test_a_card_the_log_does_not_know_gets_no_actual(in_review, monkeypatch):
    (in_review / ".histos" / "events.jsonl").write_text("", encoding="utf-8")
    now = datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc)
    assert approve_at(monkeypatch, in_review, now) is None
