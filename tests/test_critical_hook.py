"""The critical path through the real commands: repainted after every command that changes the
canvas, shown by status, and never painting over the human's arrows (cod-camino)."""
from conftest import run

from histos import canvas, config, critical

MAGENTA = config.CRITICAL_PATH_COLOR


def colors(vault):
    return {(e["fromNode"], e["toNode"]): e.get("color") for e in canvas.load(vault)["edges"]}


def chain(vault, monkeypatch, *ids):
    previous = None
    for cid in ids:
        args = ["add-card", cid, "--title", cid.upper()]
        if previous:
            args += ["--depends-on", previous, "--authorized"]
        run(monkeypatch, vault, *args)
        previous = cid


def test_adding_cards_paints_the_chain(initialized_vault, monkeypatch):
    chain(initialized_vault, monkeypatch, "a", "b", "c")
    run(monkeypatch, initialized_vault, "add-card", "x", "--title", "X")
    assert colors(initialized_vault) == {("a", "b"): MAGENTA, ("b", "c"): MAGENTA}
    assert set(critical._read_ledger(initialized_vault)) == {"a->b", "b->c"}


def test_status_shows_the_chain(initialized_vault, monkeypatch, capsys):
    chain(initialized_vault, monkeypatch, "a", "b", "c")
    capsys.readouterr()
    run(monkeypatch, initialized_vault, "status")
    assert "Critical path (3 cards, no estimates yet): a -> b -> c" in capsys.readouterr().out


def test_approving_shortens_the_path(initialized_vault, monkeypatch, tmp_path):
    v = initialized_vault
    chain(v, monkeypatch, "a", "b", "c")
    draft = tmp_path.parent / f"{tmp_path.name}-d.md"
    draft.write_text("# A\n\nReal content here.\n", encoding="utf-8")
    run(monkeypatch, v, "assign", "a")
    run(monkeypatch, v, "propose", "a", "--file", str(draft))
    run(monkeypatch, v, "approve", "a")
    assert colors(v) == {("a", "b"): None, ("b", "c"): MAGENTA}


def test_a_command_that_changes_nothing_doesnt_rewrite_the_canvas(initialized_vault, monkeypatch):
    chain(initialized_vault, monkeypatch, "a", "b")
    run(monkeypatch, initialized_vault, "status")
    path = canvas.vault_canvas_path(initialized_vault)
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    run(monkeypatch, initialized_vault, "status")
    run(monkeypatch, initialized_vault, "context", "a")
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_the_humans_arrows_survive_every_command(initialized_vault, monkeypatch):
    v = initialized_vault
    chain(v, monkeypatch, "a", "b", "c")
    data = canvas.load(v)
    next(e for e in data["edges"] if e["toNode"] == "b")["color"] = "4"  # recolored in Obsidian
    canvas.save(v, data)
    run(monkeypatch, v, "add-card", "d", "--title", "D", "--depends-on", "c", "--authorized")
    run(monkeypatch, v, "status")
    assert colors(v) == {("a", "b"): "4", ("b", "c"): MAGENTA, ("c", "d"): MAGENTA}
    assert "a->b" not in critical._read_ledger(v)


def test_arrange_keeps_the_colors(initialized_vault, monkeypatch):
    chain(initialized_vault, monkeypatch, "a", "b", "c")
    run(monkeypatch, initialized_vault, "arrange")
    assert colors(initialized_vault) == {("a", "b"): MAGENTA, ("b", "c"): MAGENTA}


def test_turning_it_off_removes_only_our_colors(initialized_vault, monkeypatch, capsys):
    v = initialized_vault
    chain(v, monkeypatch, "a", "b", "c")
    data = canvas.load(v)
    next(e for e in data["edges"] if e["toNode"] == "c")["color"] = "#00FF00"  # the human's
    canvas.save(v, data)
    cfg = config.config_path(v)
    cfg.write_text(cfg.read_text(encoding="utf-8").replace("  enabled: true\n  color:", "  enabled: false\n  color:"),
                   encoding="utf-8")
    capsys.readouterr()
    run(monkeypatch, v, "status")
    assert colors(v) == {("a", "b"): None, ("b", "c"): "#00FF00"}
    assert "Critical path" not in capsys.readouterr().out


def test_a_vault_without_config_has_no_critical_path(initialized_vault, monkeypatch):
    config.config_path(initialized_vault).unlink()
    chain(initialized_vault, monkeypatch, "a", "b")
    assert colors(initialized_vault) == {("a", "b"): None}


def test_an_old_legend_is_upgraded_on_the_next_repaint(initialized_vault, monkeypatch):
    data = canvas.load(initialized_vault)
    data["nodes"][0]["text"] = next(iter(canvas.OLDER_LEGEND_TEXTS))
    canvas.save(initialized_vault, data)
    chain(initialized_vault, monkeypatch, "a", "b")
    assert canvas.load(initialized_vault)["nodes"][0]["text"] == canvas.LEGEND_TEXT


def test_repainting_records_no_event(initialized_vault, monkeypatch):
    from histos import events
    chain(initialized_vault, monkeypatch, "a", "b")
    n = len(events.read(initialized_vault))
    run(monkeypatch, initialized_vault, "status")
    assert len(events.read(initialized_vault)) == n


def _history_with_nights(tmp_path):
    """A registered vault with 5 closed cards: 1 h of agent work, 10 h of review of which 8 h paused."""
    import json
    from test_history import approve, assign, at, created, propose, write_log, write_pauses
    from histos import events
    other = tmp_path.parent / f"{tmp_path.name}-history"
    other.mkdir()
    log, spans = [], []
    for n in range(1, 6):
        cid = f"cod-{n}"
        log += [created(at("20:00", n), cid), assign(at("21:00", n), cid), propose(at("22:00", n), cid),
                approve(at("08:00", n + 1), cid)]
        spans.append((at("23:00", n), at("07:00", n + 1)))
    write_log(other, log, vault_id="history-vault")
    write_pauses(*spans)
    registry = events.home() / "vaults.json"
    entries = json.loads(registry.read_text(encoding="utf-8"))["vaults"] if registry.exists() else []
    registry.write_text(json.dumps({"vaults": entries + [{"id": "history-vault", "path": str(other)}]}),
                        encoding="utf-8")


def test_with_history_the_path_is_in_hours_and_follows_the_review_clock(initialized_vault, monkeypatch, capsys, tmp_path):
    v = initialized_vault
    chain(v, monkeypatch, "cod-a", "cod-b")
    _history_with_nights(tmp_path)
    capsys.readouterr()
    run(monkeypatch, v, "status")
    # unclassified cards fall back to the median closed card: 1 h agent + 2 h ACTIVE review, twice
    assert "Critical path (~6 h · 2 cards): cod-a -> cod-b" in capsys.readouterr().out
    cfg = config.config_path(v)
    cfg.write_text(cfg.read_text(encoding="utf-8").replace("review: active", "review: calendar"), encoding="utf-8")
    run(monkeypatch, v, "status")
    assert "Critical path (~22 h · 2 cards): cod-a -> cod-b" in capsys.readouterr().out  # 1 + 10, twice