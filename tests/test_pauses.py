"""histos pause: the human is away, the review clock stops; their next decision ends it (3.1.2)."""
import json

from conftest import run

from histos import events, operations, pauses


def records():
    return [json.loads(line) for line in pauses.path().read_text(encoding="utf-8").splitlines()]


def test_pause_records_one_pause_for_every_vault(initialized_vault, monkeypatch, capsys):
    assert run(monkeypatch, initialized_vault, "pause") == 0
    assert "paused at" in capsys.readouterr().out
    assert pauses.path().parent == events.home()  # next to the vault registry, not in a vault
    [record] = records()
    assert record["event"] == "pause" and record["ts"].endswith("Z")
    assert pauses.current() is not None


def test_pausing_twice_keeps_the_first(initialized_vault, monkeypatch, capsys):
    run(monkeypatch, initialized_vault, "pause")
    first = pauses.current()
    capsys.readouterr()
    run(monkeypatch, initialized_vault, "pause")
    assert "already paused since" in capsys.readouterr().out
    assert pauses.current() == first and len(records()) == 1


def test_a_decision_ends_the_pause(initialized_vault, monkeypatch, tmp_path):
    v = initialized_vault
    draft = tmp_path.parent / f"{tmp_path.name}-d.md"
    draft.write_text("# A\n\nSome real content.\n", encoding="utf-8")
    run(monkeypatch, v, "add-card", "a", "--title", "A")
    run(monkeypatch, v, "assign", "a")
    run(monkeypatch, v, "propose", "a", "--file", str(draft))
    run(monkeypatch, v, "pause")
    run(monkeypatch, v, "approve", "a")
    assert pauses.current() is None
    [pause] = pauses.read()
    assert pause.end is not None and pause.end >= pause.start
    assert records()[-1]["by"] == "approve"


def test_the_agents_commands_never_end_it(initialized_vault, monkeypatch, tmp_path):
    v = initialized_vault
    run(monkeypatch, v, "pause")
    run(monkeypatch, v, "add-card", "a", "--title", "A")
    run(monkeypatch, v, "assign", "a")
    run(monkeypatch, v, "status")  # the agent runs it all night long
    run(monkeypatch, v, "context", "a")
    assert pauses.current() is not None


def test_the_desktop_app_ends_it_too(initialized_vault, monkeypatch):
    run(monkeypatch, initialized_vault, "pause")
    run(monkeypatch, initialized_vault, "add-card", "a", "--title", "A")
    operations.arrange(initialized_vault, set_aside_done=True, prune_redundant=True)
    assert pauses.current() is None and records()[-1]["by"] == "arrange"


def test_every_decision_counts_and_nothing_else():
    assert pauses.DECISIONS == {"approve", "reject", "retry", "accept-edit", "arrange",
                                "guide approve", "guide reject", "plan approve", "plan reject"}


def test_status_says_so(initialized_vault, monkeypatch, capsys):
    run(monkeypatch, initialized_vault, "pause")
    capsys.readouterr()
    run(monkeypatch, initialized_vault, "status")
    assert "Paused since" in capsys.readouterr().out


def test_a_broken_line_is_skipped():
    pauses.path().parent.mkdir(parents=True, exist_ok=True)
    pauses.path().write_text('{"event": "pause", "ts": "2026-11-08T22:00:00.000Z"}\n'
                             '{"event": "resume", "ts": "2026-11-09T08:00:00.000Z", "by": "approve"}\n'
                             '{"event": "pau', encoding="utf-8")
    [pause] = pauses.read()
    assert (pause.end - pause.start).total_seconds() == 10 * 3600


def test_nothing_recorded_means_never_paused():
    assert pauses.read() == [] and pauses.current() is None
