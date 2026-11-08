"""history.py: closed cards and their two times, from the event log (cod-historial).

Logs are written by hand with round hours so every expected figure can be checked at a glance."""
import json
from datetime import datetime, timezone

import pytest
from conftest import run

from histos import events, history

B, P, R, A, BL = "Backlog", "In progress", "Proposal pending review", "Approved", "Blocked"


def at(hhmm: str, day: int = 1) -> str:
    h, m = hhmm.split(":")
    return f"2026-12-{day:02d}T{int(h):02d}:{int(m):02d}:00.000Z"


def ev(ts, name, card=None, worker=None, data=None, transitions=None):
    e = {"v": 1, "ts": ts, "event": name, "card": card, "actor": "agent", "worker": worker}
    if data:
        e["data"] = data
    if transitions:
        e["transitions"] = [{"card": c, "from": f, "to": t} for c, f, t in transitions]
    return e


def created(ts, card, deps=()):
    return ev(ts, "card_created", card, data={"title": card, "depends_on": list(deps)},
              transitions=[(card, None, BL if deps else B)])


def assign(ts, card, worker="parent", takeover=False, transitions=True):
    return ev(ts, "assigned", card, worker, {"by": "agent", "takeover": takeover, "previous_worker": None},
              [(card, B, P)] if transitions else None)


def propose(ts, card, worker="parent", attempt=1):
    return ev(ts, "proposed", card, worker, {"attempt": attempt, "words": 10, "lines": 2}, [(card, P, R)])


def refuse(ts, card, attempt, worker="parent"):
    return ev(ts, "proposal_refused", card, worker, {"attempt": attempt, "failed": ["x"]})


def approve(ts, card):
    return ev(ts, "approved", card, None, {"words": 10, "lines": 2}, [(card, R, A)])


def reject(ts, card):
    return ev(ts, "rejected", card, None, {"feedback": "no"}, [(card, R, B)])


def write_log(vault, log, vault_id="v-1"):
    folder = vault / ".histos"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in log), encoding="utf-8")
    (folder / "vault.json").write_text(json.dumps({"id": vault_id}), encoding="utf-8")
    return vault


def one(vault):
    [s] = history.vault_samples(vault)
    return s


# 1. A clean round
def test_a_clean_round(tmp_path):
    write_log(tmp_path, [created(at("09:00"), "a"), assign(at("10:00"), "a"),
                         propose(at("10:30"), "a"), approve(at("14:30"), "a")])
    s = one(tmp_path)
    assert (s.agent_hours, s.review_hours, s.rounds, s.rejections) == (0.5, 4.0, 1, 0)
    assert s.vault == "v-1" and s.worker_kind == "parent" and not s.approximate


# 2. A rejection and a second round; Backlog in between doesn't count
def test_a_rejection_adds_both_rounds(tmp_path):
    write_log(tmp_path, [created(at("09:00"), "a"), assign(at("10:00"), "a"), propose(at("11:00"), "a"),
                         reject(at("12:00"), "a"),
                         assign(at("15:00"), "a"), propose(at("15:30"), "a", attempt=1), approve(at("16:00"), "a")])
    s = one(tmp_path)
    assert (s.agent_hours, s.review_hours, s.rounds, s.rejections) == (1.5, 1.5, 2, 1)


# 3. Out of attempts: the wait for the human's retry is review time
def test_waiting_for_a_retry_counts_as_review(tmp_path):
    write_log(tmp_path, [created(at("09:00"), "a"), assign(at("10:00"), "a"),
                         refuse(at("10:10"), "a", 1), refuse(at("10:20"), "a", 2), refuse(at("10:30"), "a", 3),
                         ev(at("12:30"), "retry", "a"),
                         propose(at("13:00"), "a"), approve(at("14:00"), "a")])
    s = one(tmp_path)
    assert s.agent_hours == pytest.approx(1.0)   # 10:00-10:30 + 12:30-13:00
    assert s.review_hours == pytest.approx(3.0)  # 10:30-12:30 waiting + 13:00-14:00 in review


# 4. A batch assigned at once is worked one card at a time
def test_an_afk_batch_is_not_parallel_work(tmp_path):
    cards = ["c1", "c2", "c3", "c4", "c5"]
    log = [created(at("09:00"), c) for c in cards]
    log += [assign(at("10:00"), c, transitions=False) for c in cards[:-1]]
    log.append(ev(at("10:00"), "assigned", "c5", "parent", {"by": "agent", "takeover": False},
                  [(c, B, P) for c in cards]))  # one session: transitions on the last event
    for n, c in enumerate(cards, start=1):
        log.append(propose(at(f"10:{20 * n}" if n < 3 else f"{10 + (20 * n) // 60}:{(20 * n) % 60:02d}"), c))
    log += [approve(at("16:00"), c) for c in cards]
    write_log(tmp_path, log)
    assert [round(s.agent_hours * 60) for s in history.vault_samples(tmp_path)] == [20] * 5


# 5. Two subagents in parallel keep their own times
def test_subagents_work_in_parallel(tmp_path):
    write_log(tmp_path, [created(at("09:00"), "a"), created(at("09:00"), "b"),
                         assign(at("10:00"), "a", "sub-a"), assign(at("10:00"), "b", "sub-b"),
                         propose(at("11:00"), "a", "sub-a"), propose(at("11:30"), "b", "sub-b"),
                         approve(at("12:00"), "a"), approve(at("12:00"), "b")])
    by_card = {s.card: s for s in history.vault_samples(tmp_path)}
    assert (by_card["a"].agent_hours, by_card["b"].agent_hours) == (1.0, 1.5)
    assert by_card["a"].worker_kind == "sub"


# 6. A takeover doesn't cut the clock
def test_a_takeover_keeps_the_dead_workers_time(tmp_path):
    write_log(tmp_path, [created(at("09:00"), "x"), created(at("09:00"), "a"),
                         assign(at("09:00"), "x"), propose(at("13:00"), "x"),  # parent busy until 13:00
                         assign(at("10:00"), "a", "sub-a"),
                         assign(at("12:00"), "a", "parent", takeover=True, transitions=False),
                         propose(at("14:00"), "a"), approve(at("15:00"), "a"), approve(at("15:00"), "x")])
    a = {s.card: s for s in history.vault_samples(tmp_path)}["a"]
    assert a.agent_hours == pytest.approx(3.0)  # sub-a 10:00-12:00, then parent from 13:00 (it was busy) to 14:00


# 7. A card already under way when the log started gives no sample
def test_a_card_started_before_the_log_is_left_out(tmp_path):
    write_log(tmp_path, [propose(at("10:00"), "old"), approve(at("11:00"), "old")])
    assert history.vault_samples(tmp_path) == []


# 8. Colors changed by hand during the card's life make it approximate
def test_hand_changes_mark_the_sample_approximate(tmp_path):
    write_log(tmp_path, [created(at("09:00"), "a"), assign(at("10:00"), "a"),
                         ev(at("10:10"), "state_recomputed", data={"seen_outside_histos": True},
                            transitions=[("a", P, P + "")]),
                         propose(at("10:30"), "a"), approve(at("11:00"), "a")])
    assert one(tmp_path).approximate


# 10. Tier, model and dependencies: the last value before approval (a late model yes still counts)
def test_traits_come_from_the_events(tmp_path):
    write_log(tmp_path, [created(at("09:00"), "a", deps=["d1"]),
                         ev(at("09:01"), "dependency_added", "a", data={"depends_on": ["d2", "d1"]}),
                         ev(at("09:30"), "classified", "a", data={"tier": "fast"}),
                         ev(at("09:40"), "classified", "a", data={"tier": "exec"}),
                         ev(at("09:50"), "state_recomputed", transitions=[("a", BL, B)]),
                         assign(at("10:00"), "a"), propose(at("10:30"), "a"), approve(at("11:00"), "a"),
                         ev(at("11:01"), "model_approved", "a", data={"model": "Opus 5.5"})])
    s = one(tmp_path)
    assert (s.tier, s.model, s.dependencies) == ("exec", "Opus 5.5", 2)


# 9. Every registered vault, each once; missing ones are skipped
def test_samples_reads_every_registered_vault(tmp_path):
    home, v1, v2 = tmp_path / "home", tmp_path / "v1", tmp_path / "v2"
    for vault, vid in ((v1, "one"), (v2, "two")):
        vault.mkdir()
        write_log(vault, [created(at("09:00"), "a"), assign(at("10:00"), "a"),
                          propose(at("11:00"), "a"), approve(at("12:00"), "a")], vault_id=vid)
    home.mkdir()
    entries = [{"id": "one", "path": str(v1)}, {"id": "one-again", "path": str(v1)},
               {"id": "two", "path": str(v2)}, {"id": "gone", "path": str(tmp_path / "gone")}]
    (home / "vaults.json").write_text(json.dumps({"vaults": entries}), encoding="utf-8")
    skipped = []
    found = history.samples(home, skipped)
    assert sorted(s.vault for s in found) == ["one", "two"]
    assert len(skipped) == 1 and "gone" in skipped[0]


def test_no_registry_means_no_samples(tmp_path):
    assert history.samples(tmp_path / "nowhere") == []


# 11. What an open card has so far
def test_open_progress(tmp_path):
    write_log(tmp_path, [created(at("09:00"), "w"), created(at("09:00"), "r"), created(at("09:00"), "todo"),
                         assign(at("10:00"), "w"),
                         assign(at("08:00"), "r", "sub-r"), propose(at("09:00"), "r", "sub-r")])
    now = datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc)
    progress = history.open_progress(tmp_path, now)
    assert (progress["w"].state, progress["w"].agent_hours, progress["w"].review_hours) == (P, 2.0, 0.0)
    assert (progress["r"].state, progress["r"].agent_hours, progress["r"].review_hours) == (R, 1.0, 3.0)
    assert progress["todo"] == history.Progress(B, 0.0, 0.0)


# 12. The log the CLI really writes is the log this module reads
def test_a_real_cli_log(initialized_vault, monkeypatch, tmp_path):
    v = initialized_vault
    draft = tmp_path.parent / f"{tmp_path.name}-draft.md"
    draft.write_text("# A\n\nA real draft with enough words.\n", encoding="utf-8")
    run(monkeypatch, v, "add-card", "a", "--title", "A")
    run(monkeypatch, v, "add-card", "b", "--title", "B", "--depends-on", "a", "--authorized")
    run(monkeypatch, v, "classify", "a")
    run(monkeypatch, v, "assign", "a", "--worker", "parent")
    run(monkeypatch, v, "propose", "a", "--file", str(draft))
    run(monkeypatch, v, "approve", "a")
    run(monkeypatch, v, "model", "a", "--approved", "Opus 5.5", "--authorized")
    s = one(v)
    assert s.card == "a" and s.rounds == 1 and s.worker_kind == "parent" and s.model == "Opus 5.5"
    assert s.tier in ("fast", "exec", "reasoning") and s.agent_hours >= 0 and s.review_hours >= 0
    assert history.samples() and history.samples()[0].card == "a"  # via the test's HISTOS_HOME registry
    assert set(history.open_progress(v)) == {"b"}
    assert events.read(v)  # sanity: the log is there
