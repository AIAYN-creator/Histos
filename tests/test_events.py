"""The event log module: sessions, transitions, the append-only file and the vault registry."""
import json
import os

import pytest

from histos import canvas, events, lock


def _set_color(vault, card_id, color):
    data = canvas.load(vault)
    canvas.find_card(data, card_id)["color"] = color
    canvas.save(vault, data)


def _vault_with(vault, *ids):
    data = canvas.load(vault)
    for card_id in ids:
        canvas.add_card_node(data, card_id, canvas.BACKLOG)
    canvas.save(vault, data)


def test_nothing_is_written_when_nothing_happened(initialized_vault):
    with lock.vault_lock(initialized_vault, "noop"):
        pass
    assert not events.log_path(initialized_vault).exists()


def test_emitted_events_are_written_on_release(initialized_vault):
    with lock.vault_lock(initialized_vault, "x"):
        events.emit("arranged", pruned=2)
        assert not events.log_path(initialized_vault).exists()  # only on release
    [record] = events.read(initialized_vault)
    assert record["event"] == "arranged" and record["actor"] == "agent" and record["card"] is None
    assert record["data"] == {"pruned": 2} and record["v"] == 1
    assert record["ts"].endswith("Z") and "T" in record["ts"]


def test_the_actor_comes_from_the_lock(initialized_vault):
    with lock.vault_lock(initialized_vault, "approve", actor="human"):
        events.emit("arranged")
    assert events.read(initialized_vault)[0]["actor"] == "human"


def test_transitions_include_other_cards_and_go_on_the_last_event(initialized_vault):
    _vault_with(initialized_vault, "a", "b")
    with lock.vault_lock(initialized_vault, "x"):
        _set_color(initialized_vault, "a", canvas.APPROVED)
        _set_color(initialized_vault, "b", canvas.IN_PROGRESS)
        events.emit("card_created", card="c")
        events.emit("approved", card="a")
    first, last = events.read(initialized_vault)
    assert "transitions" not in first
    assert last["transitions"] == [
        {"card": "a", "from": "Backlog", "to": "Approved"},
        {"card": "b", "from": "Backlog", "to": "In progress"},
    ]


def test_a_new_card_transitions_from_nothing(initialized_vault):
    with lock.vault_lock(initialized_vault, "x"):
        _vault_with(initialized_vault, "a")
        events.emit("card_created", card="a")
    assert events.read(initialized_vault)[0]["transitions"] == [{"card": "a", "from": None, "to": "Backlog"}]


def test_changes_nobody_emitted_are_recorded_as_recomputed(initialized_vault):
    _vault_with(initialized_vault, "a")
    with lock.vault_lock(initialized_vault, "status", actor="human"):
        _set_color(initialized_vault, "a", canvas.BLOCKED)
    [record] = events.read(initialized_vault)
    assert record["event"] == "state_recomputed"
    assert record["transitions"] == [{"card": "a", "from": "Backlog", "to": "Blocked"}]


def test_an_exception_records_nothing(initialized_vault):
    with pytest.raises(RuntimeError):
        with lock.vault_lock(initialized_vault, "boom"):
            events.emit("arranged")
            raise RuntimeError("x")
    assert events.read(initialized_vault) == []


def test_a_nested_session_joins_the_outer_one(initialized_vault):
    with events.recording(initialized_vault, "agent"):
        events.emit("arranged")
        with events.recording(initialized_vault, "human"):
            events.emit("retry", card="a")
    assert [(e["event"], e["actor"]) for e in events.read(initialized_vault)] == [
        ("arranged", "agent"), ("retry", "agent"),
    ]


def test_emit_outside_a_lock_is_a_programming_error(initialized_vault):
    with pytest.raises(RuntimeError, match="outside a vault lock"):
        events.emit("arranged")


def test_unknown_events_and_actors_are_refused(initialized_vault):
    with pytest.raises(ValueError):
        with lock.vault_lock(initialized_vault, "x"):
            events.emit("made_up")
    with pytest.raises(ValueError):
        with events.recording(initialized_vault, "robot"):
            pass


def test_the_worker_is_read_from_the_card(initialized_vault):
    _vault_with(initialized_vault, "a")
    (initialized_vault / "content" / "a.md").write_text("---\nworker: sub-a\n---\nbody\n", encoding="utf-8")
    with lock.vault_lock(initialized_vault, "x"):
        events.emit("proposed", card="a")
        events.emit("proposed", card="ghost")
    assert [e["worker"] for e in events.read(initialized_vault)] == ["sub-a", None]


def test_sessions_append_and_never_rewrite(initialized_vault):
    for n in range(3):
        with lock.vault_lock(initialized_vault, "x"):
            events.emit("arranged", pruned=n)
    lines = events.log_path(initialized_vault).read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["data"]["pruned"] for line in lines] == [0, 1, 2]


def test_a_cut_off_last_line_is_skipped(initialized_vault):
    with lock.vault_lock(initialized_vault, "x"):
        events.emit("arranged")
    with open(events.log_path(initialized_vault), "a", encoding="utf-8") as f:
        f.write('{"v": 1, "ts": "2026-')
    assert len(events.read(initialized_vault)) == 1
    errors, warnings = events.check_log(initialized_vault)
    assert errors == [] and "line 2 is cut off" in warnings[0]


def test_a_broken_line_in_the_middle_is_an_error(initialized_vault):
    with lock.vault_lock(initialized_vault, "x"):
        events.emit("arranged")
    path = events.log_path(initialized_vault)
    path.write_text("not json\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    errors, _ = events.check_log(initialized_vault)
    assert errors == ["events: line 1 is not valid JSON"]


def test_check_log_validates_against_the_schema(initialized_vault):
    path = events.log_path(initialized_vault)
    path.parent.mkdir()
    path.write_text('{"v": 1, "ts": "2026-11-08T10:00:00.000Z", "event": "made_up", "card": null, '
                    '"actor": "agent", "worker": null}\n', encoding="utf-8")
    errors, _ = events.check_log(initialized_vault)
    assert len(errors) == 1 and "line 1: event" in errors[0]


def test_what_the_code_writes_passes_the_schema(initialized_vault):
    with lock.vault_lock(initialized_vault, "x"):
        _vault_with(initialized_vault, "a")
        events.emit("card_created", card="a", title="A", depends_on=[])
    assert events.check_log(initialized_vault) == ([], [])


def test_the_schema_and_the_code_list_the_same_events():
    from histos.validation import _load_named_schema
    assert set(_load_named_schema("histos-events.schema.json")["properties"]["event"]["enum"]) == events.EVENTS


def test_a_failed_write_warns_and_never_fails_the_command(initialized_vault, monkeypatch, capsys):
    def broken(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(events, "_append", broken)
    with lock.vault_lock(initialized_vault, "x"):
        events.emit("arranged")
    assert "couldn't record the event (disk full)" in capsys.readouterr().err


def test_the_vault_gets_a_stable_id(initialized_vault):
    for _ in range(2):
        with lock.vault_lock(initialized_vault, "x"):
            events.emit("arranged")
    info = json.loads((initialized_vault / ".histos" / "vault.json").read_text(encoding="utf-8"))
    assert len(info["id"]) == 36


def test_the_vault_is_registered_in_histos_home(initialized_vault, tmp_path_factory):
    with lock.vault_lock(initialized_vault, "x"):
        events.emit("arranged")
    registry = json.loads((events.home() / "vaults.json").read_text(encoding="utf-8"))
    vault_id = json.loads((initialized_vault / ".histos" / "vault.json").read_text(encoding="utf-8"))["id"]
    assert registry == {"vaults": [{"id": vault_id, "path": str(initialized_vault.resolve())}]}


def test_tests_never_touch_the_real_home():
    assert os.environ["HISTOS_HOME"] and events.home() != events.Path.home() / ".histos"


def test_a_moved_vault_updates_its_registry_entry(initialized_vault, tmp_path):
    with lock.vault_lock(initialized_vault, "x"):
        events.emit("arranged")
    moved = tmp_path.parent / (tmp_path.name + "-moved")
    initialized_vault.rename(moved)
    with lock.vault_lock(moved, "x"):
        events.emit("arranged")
    [entry] = json.loads((events.home() / "vaults.json").read_text(encoding="utf-8"))["vaults"]
    assert entry["path"] == str(moved.resolve())


def test_size_of_counts_words_like_the_eval():
    assert events.size_of("Hola, mundo.\nSegunda línea") == {"words": 4, "lines": 2}
