"""What each command records in the event log (cod-eventos)."""
from conftest import run

from histos import canvas, events, operations


def log(vault):
    return events.read(vault)


def kinds(vault):
    return [(e["event"], e["card"], e["actor"]) for e in log(vault)]


def draft(tmp_path, text="# Draft\n\nSome real content here.\n"):
    path = tmp_path.parent / f"{tmp_path.name}-draft.md"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_init_records_the_vault_creation(initialized_vault):
    [record] = log(initialized_vault)
    assert record["event"] == "vault_created" and record["actor"] == "human"
    assert record["data"]["version"]


def test_board_commands(initialized_vault, monkeypatch):
    v = initialized_vault
    run(monkeypatch, v, "add-card", "a", "--title", "A")
    run(monkeypatch, v, "add-card", "b", "--title", "B", "--depends-on", "a", "--authorized")
    run(monkeypatch, v, "add-card", "c", "--title", "C")
    run(monkeypatch, v, "link", "c", "--depends-on", "a", "--authorized")
    run(monkeypatch, v, "describe", "a", "--text", "new")
    assert kinds(v)[1:] == [
        ("card_created", "a", "agent"), ("card_created", "b", "agent"), ("card_created", "c", "agent"),
        ("dependency_added", "c", "agent"), ("described", "a", "agent"),
    ]
    created_b, linked, described = log(v)[2], log(v)[4], log(v)[5]
    assert created_b["data"] == {"title": "B", "depends_on": ["a"]}
    assert created_b["transitions"] == [{"card": "b", "from": None, "to": "Blocked"}]
    assert linked["transitions"] == [{"card": "c", "from": "Backlog", "to": "Blocked"}]
    assert described["data"] == {"fields": ["description"]}


def test_failed_commands_record_nothing(initialized_vault, monkeypatch):
    before = len(log(initialized_vault))
    assert run(monkeypatch, initialized_vault, "add-card", "a", "--title", "A", "--depends-on", "zz",
               "--authorized") == 1
    assert run(monkeypatch, initialized_vault, "assign", "nope") == 1
    assert len(log(initialized_vault)) == before


def test_assign_and_takeover(initialized_vault, monkeypatch):
    v = initialized_vault
    run(monkeypatch, v, "add-card", "a", "--title", "A")
    run(monkeypatch, v, "assign", "a", "--worker", "sub-a")
    run(monkeypatch, v, "assign", "a", "--worker", "parent", "--takeover")
    first, second = log(v)[-2:]
    assert first["data"] == {"by": "agent", "takeover": False, "previous_worker": None}
    assert first["worker"] == "sub-a" and first["transitions"][0]["to"] == "In progress"
    assert second["data"] == {"by": "agent", "takeover": True, "previous_worker": "sub-a"}
    assert second["worker"] == "parent" and "transitions" not in second


def test_routing_commands(initialized_vault, monkeypatch):
    v = initialized_vault
    run(monkeypatch, v, "add-card", "a", "--title", "A")
    run(monkeypatch, v, "classify", "a", "--dry-run")
    assert log(v)[-1]["event"] == "card_created"  # a dry run records nothing
    run(monkeypatch, v, "classify", "a")
    run(monkeypatch, v, "model", "a", "--proposed", "Sonnet 5.5")
    run(monkeypatch, v, "model", "a", "--approved", "Opus 5.5", "--authorized")
    run(monkeypatch, v, "model", "a", "--clear")
    classified, proposed, approved, cleared = log(v)[-4:]
    assert classified["event"] == "classified" and classified["data"]["previous_tier"] is None
    assert classified["data"]["model_cleared"] is False and classified["data"]["tier"] in ("fast", "exec", "reasoning")
    assert (proposed["event"], proposed["data"]) == ("model_proposed", {"model": "Sonnet 5.5"})
    assert (approved["event"], approved["data"]) == ("model_approved", {"model": "Opus 5.5"})
    assert cleared["event"] == "model_cleared"


def test_the_review_cycle(initialized_vault, monkeypatch, tmp_path):
    v = initialized_vault
    run(monkeypatch, v, "add-card", "a", "--title", "A")
    run(monkeypatch, v, "add-card", "b", "--title", "B", "--depends-on", "a", "--authorized")
    run(monkeypatch, v, "assign", "a", "--worker", "parent")
    assert run(monkeypatch, v, "propose", "a", "--file", draft(tmp_path, "# A\n\nTODO\n")) == 1
    assert run(monkeypatch, v, "propose", "a", "--file", draft(tmp_path)) == 0
    run(monkeypatch, v, "reject", "a", "--feedback", "shorter")
    run(monkeypatch, v, "assign", "a")
    run(monkeypatch, v, "propose", "a", "--file", draft(tmp_path))
    run(monkeypatch, v, "approve", "a")

    refused, proposed, rejected = log(v)[4:7]
    assert refused["event"] == "proposal_refused" and refused["actor"] == "agent"
    assert refused["data"]["attempt"] == 1 and refused["data"]["failed"] == ["no_placeholders"]
    assert "transitions" not in refused  # it stays In progress
    assert proposed["data"] == {"attempt": 2, "words": 5, "lines": 3}
    assert proposed["transitions"] == [{"card": "a", "from": "In progress", "to": "Proposal pending review"}]
    assert (rejected["event"], rejected["actor"], rejected["worker"]) == ("rejected", "human", "parent")
    assert rejected["data"] == {"feedback": "shorter"}

    approved = log(v)[-1]
    assert approved["event"] == "approved" and approved["actor"] == "human"
    assert approved["data"] == {"words": 5, "lines": 3}
    assert approved["transitions"] == [
        {"card": "a", "from": "Proposal pending review", "to": "Approved"},
        {"card": "b", "from": "Blocked", "to": "Backlog"},  # the dependent it unblocked
    ]


def test_retry_and_accept_edit(initialized_vault, monkeypatch, tmp_path):
    v = initialized_vault
    run(monkeypatch, v, "add-card", "a", "--title", "A")
    run(monkeypatch, v, "assign", "a")
    for _ in range(3):
        run(monkeypatch, v, "propose", "a", "--file", draft(tmp_path, "TODO"))
    run(monkeypatch, v, "retry", "a")
    run(monkeypatch, v, "accept-edit", "a")
    assert kinds(v)[-2:] == [("retry", "a", "human"), ("edit_accepted", "a", "human")]


def test_guides(initialized_vault, monkeypatch, tmp_path):
    v = initialized_vault
    (v / "evals" / "guides.yaml").unlink()  # any draft will do here
    run(monkeypatch, v, "guide", "propose", "PROJECT.md", "--file", draft(tmp_path, "# P\n\nOne two.\n"))
    run(monkeypatch, v, "guide", "reject", "PROJECT.md", "--feedback", "more")
    run(monkeypatch, v, "guide", "propose", "project.md", "--file", draft(tmp_path, "# P\n\nOne two.\n"))
    run(monkeypatch, v, "guide", "approve", "PROJECT.md")
    assert kinds(v)[-4:] == [
        ("guide_proposed", None, "agent"), ("guide_rejected", None, "human"),
        ("guide_proposed", None, "agent"), ("guide_approved", None, "human"),
    ]
    assert log(v)[-3]["data"] == {"file": "PROJECT.md", "feedback": "more", "recorded_in": ".histos/decisions.md"}
    assert log(v)[-1]["data"] == {"file": "PROJECT.md", "words": 3, "lines": 3}


def test_arrange_and_add_config(initialized_vault, monkeypatch):
    v = initialized_vault
    run(monkeypatch, v, "add-card", "a", "--title", "A")
    run(monkeypatch, v, "arrange")
    assert log(v)[-1]["event"] == "arranged" and log(v)[-1]["actor"] == "human"
    run(monkeypatch, v, "init", "--add-config")
    assert log(v)[-1]["event"] == "arranged"  # nothing was missing: nothing recorded
    (v / "histos.config.yaml").unlink()
    run(monkeypatch, v, "init", "--add-config")
    assert (log(v)[-1]["event"], log(v)[-1]["data"]) == ("config_added", {"files": ["histos.config.yaml"]})


def test_status_records_only_changes_it_sees(initialized_vault, monkeypatch):
    v = initialized_vault
    run(monkeypatch, v, "add-card", "a", "--title", "A")
    run(monkeypatch, v, "add-card", "b", "--title", "B", "--depends-on", "a", "--authorized")
    n = len(log(v))
    run(monkeypatch, v, "status")
    assert len(log(v)) == n
    data = canvas.load(v)  # "approved" by hand in Obsidian: b should unblock on the next status
    canvas.find_card(data, "a")["color"] = canvas.APPROVED
    canvas.save(v, data)
    run(monkeypatch, v, "status")
    outside, recomputed = log(v)[n:]
    assert outside["data"] == {"seen_outside_histos": True}
    assert outside["transitions"] == [{"card": "a", "from": "Backlog", "to": "Approved"}]
    assert recomputed["event"] == "state_recomputed" and recomputed["actor"] == "human"
    assert "data" not in recomputed
    assert recomputed["transitions"] == [{"card": "b", "from": "Blocked", "to": "Backlog"}]


def test_the_desktop_app_records_as_human(initialized_vault, monkeypatch, tmp_path):
    v = initialized_vault
    run(monkeypatch, v, "add-card", "a", "--title", "A")
    run(monkeypatch, v, "assign", "a")
    run(monkeypatch, v, "propose", "a", "--file", draft(tmp_path))
    operations.approve(v, "a")  # what the GUI calls
    assert kinds(v)[-1] == ("approved", "a", "human")


def test_read_only_commands_record_nothing(initialized_vault, monkeypatch, tmp_path):
    v = initialized_vault
    run(monkeypatch, v, "add-card", "a", "--title", "A")
    n = len(log(v))
    run(monkeypatch, v, "context", "a")
    run(monkeypatch, v, "validate")
    run(monkeypatch, v, "eval", "a", "--file", draft(tmp_path))
    assert len(log(v)) == n


def test_validate_reports_a_broken_log(initialized_vault, monkeypatch, capsys):
    path = events.log_path(initialized_vault)
    path.write_text("garbage\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    assert run(monkeypatch, initialized_vault, "validate") == 1
    assert "events: line 1 is not valid JSON" in capsys.readouterr().out


def test_validate_only_warns_about_a_cut_off_last_line(initialized_vault, monkeypatch, capsys):
    with open(events.log_path(initialized_vault), "a", encoding="utf-8") as f:
        f.write('{"v": 1')
    assert run(monkeypatch, initialized_vault, "validate") == 0
    assert "cut off" in capsys.readouterr().err
