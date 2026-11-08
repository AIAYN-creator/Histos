"""`histos status` and the card frontmatter show the estimates (cod-estimador).

The history is synthetic: a second vault whose event log is written by hand and registered in the
test's HISTOS_HOME, so every figure is known beforehand."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from conftest import run
from test_history import B, P, R, A, ev, write_log  # the hand-written log helpers

from histos import events, frontmatter

START = datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)


def iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def closed_card(n, card, tier, agent_min, review_h):
    """The events of one card that lived on its own day: assigned, proposed, approved."""
    day = START + timedelta(days=n)
    proposed = day + timedelta(minutes=agent_min)
    approved = proposed + timedelta(hours=review_h)
    return [
        ev(iso(day - timedelta(hours=1)), "card_created", card, data={"title": card, "depends_on": []},
           transitions=[(card, None, B)]),
        ev(iso(day - timedelta(minutes=30)), "classified", card, data={"tier": tier}),
        ev(iso(day), "assigned", card, "parent", {"by": "agent", "takeover": False, "previous_worker": None},
           [(card, B, P)]),
        ev(iso(proposed), "proposed", card, "parent", {"attempt": 1, "words": 10, "lines": 2}, [(card, P, R)]),
        ev(iso(approved), "approved", card, None, {"words": 10, "lines": 2}, [(card, R, A)]),
    ]


def register(vault, vault_id="hist"):
    home = events.home()
    home.mkdir(parents=True, exist_ok=True)
    path = home / "vaults.json"
    known = json.loads(path.read_text(encoding="utf-8"))["vaults"] if path.exists() else []
    known.append({"id": vault_id, "path": str(vault)})
    path.write_text(json.dumps({"vaults": known}), encoding="utf-8")


@pytest.fixture
def history_vault(tmp_path_factory):
    """Ten exec `cod-` cards (agent 25 min / 50 min, review 3 h / 24 h) and two reasoning ones."""
    vault = tmp_path_factory.mktemp("history")
    log = []
    for n in range(10):
        slow = n >= 7
        log += closed_card(n, f"cod-h{n}", "exec", 50 if slow else 25, 24 if slow else 3)
    for n in range(10, 12):
        log += closed_card(n, f"cod-h{n}", "reasoning", 25, 3)
    write_log(vault, log, vault_id="hist")
    register(vault)
    return vault


@pytest.fixture
def vault(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    for card in ("cod-open1", "cod-open2", "cod-open3"):
        run(monkeypatch, tmp_path, "add-card", card, "--title", card)
    return tmp_path


def set_meta(vault, card_id, **fields):
    md = vault / "content" / f"{card_id}.md"
    meta, body = frontmatter.read(md)
    meta.update(fields)
    frontmatter.write(md, meta, body)


def status(vault, monkeypatch, capsys):
    capsys.readouterr()
    run(monkeypatch, vault, "status")
    return capsys.readouterr().out


def test_the_three_labels(history_vault, vault, monkeypatch, capsys):
    set_meta(vault, "cod-open1", proposed_tier="exec", proposed_model="Sonnet 5.5", approved_model="Sonnet 5.5")
    set_meta(vault, "cod-open3", proposed_tier="reasoning")
    out = status(vault, monkeypatch, capsys)
    assert ("  - cod-open1  (content/cod-open1.md)  "
            "{exec · Sonnet 5.5 · agent ~25 min (≤50 min) · review ~3 h (≤1 d)}") in out
    assert "  - cod-open2  (content/cod-open2.md)  {unclassified · review ~3 h (≤1 d)}" in out
    assert ("  - cod-open3  (content/cod-open3.md)  "
            "{reasoning · agent no data (3 more needed) · review ~3 h (≤1 d)}") in out


def test_approved_cards_have_no_estimate(history_vault, vault, monkeypatch, capsys):
    run(monkeypatch, vault, "add-card", "cod-done", "--title", "done")
    from histos import canvas
    data = canvas.load(vault)
    canvas.find_card(data, "cod-done")["color"] = canvas.APPROVED
    canvas.save(vault, data)
    out = status(vault, monkeypatch, capsys)
    assert "  - cod-done  (content/cod-done.md)\n" in out


def test_the_review_estimate_leaves_out_the_hours_the_human_was_away(tmp_path_factory, vault, monkeypatch, capsys):
    hist = tmp_path_factory.mktemp("history")
    log = []
    for n in range(5):  # 24 h of calendar review each, 21 of them paused
        log += closed_card(n, f"card{n}", "exec", 30, 24)
    write_log(hist, log, vault_id="hist")
    register(hist)
    lines = []
    for n in range(5):
        day = START + timedelta(days=n)
        lines.append(json.dumps({"event": "pause", "ts": iso(day + timedelta(minutes=30, hours=1))}))
        lines.append(json.dumps({"event": "resume", "ts": iso(day + timedelta(minutes=30, hours=22)), "by": "approve"}))
    (events.home() / "pauses.jsonl").write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    set_meta(vault, "cod-open1", proposed_tier="exec")
    out = status(vault, monkeypatch, capsys)
    assert "{exec · agent ~30 min (≤30 min) · review ~3 h (≤3 h)}" in out
    meta, _ = frontmatter.read(vault / "content" / "cod-open1.md")
    assert meta["estimate"]["review_hours"]["median"] == 3.0  # named review_hours, computed from active hours


def test_the_frontmatter_holds_the_estimate(history_vault, vault, monkeypatch, capsys):
    set_meta(vault, "cod-open1", proposed_tier="exec")
    set_meta(vault, "cod-open3", proposed_tier="reasoning")
    status(vault, monkeypatch, capsys)
    meta, _ = frontmatter.read(vault / "content" / "cod-open1.md")
    assert meta["estimate"]["agent_hours"] == {"median": 0.42, "p80": 0.83, "basis": "exec · cod · 0 deps · 10 cards"}
    assert meta["estimate"]["review_hours"] == {"median": 3.0, "p80": 24.0, "basis": "cod · 12 cards"}
    assert isinstance(meta["estimate"]["at"], str)
    assert meta["estimated_duration_hours"] == 3.42  # agent median + review median
    partial, _ = frontmatter.read(vault / "content" / "cod-open3.md")
    assert partial["estimate"]["agent_hours"] is None and partial["estimate"]["review_hours"]["median"] == 3.0
    assert partial["estimated_duration_hours"] is None  # one half is missing
    capsys.readouterr()
    assert run(monkeypatch, vault, "validate") == 0
    assert "OK" in capsys.readouterr().out


def test_the_file_is_only_written_when_the_numbers_change(history_vault, vault, monkeypatch, capsys):
    set_meta(vault, "cod-open1", proposed_tier="exec")
    status(vault, monkeypatch, capsys)
    one, two = (vault / "content" / f"{c}.md" for c in ("cod-open1", "cod-open2"))
    before = {p: p.stat().st_mtime_ns for p in (one, two)}
    status(vault, monkeypatch, capsys)
    assert {p: p.stat().st_mtime_ns for p in (one, two)} == before  # nothing changed: not touched
    set_meta(vault, "cod-open2", proposed_tier="exec")  # a tier: its agent estimate appears
    after_set = two.stat().st_mtime_ns
    status(vault, monkeypatch, capsys)
    assert two.stat().st_mtime_ns > after_set
    assert one.stat().st_mtime_ns == before[one]
    meta, _ = frontmatter.read(two)
    assert meta["estimated_duration_hours"] == 3.42


def test_classify_refreshes_the_estimate(history_vault, vault, monkeypatch, capsys):
    run(monkeypatch, vault, "classify", "cod-open1")
    meta, _ = frontmatter.read(vault / "content" / "cod-open1.md")
    assert meta["estimate"]["review_hours"]["median"] == 3.0
    assert meta["proposed_tier"] in ("fast", "exec", "reasoning")


def test_no_history_at_all_is_not_an_error(vault, monkeypatch, capsys):
    out = status(vault, monkeypatch, capsys)
    assert "{unclassified · review no data (5 more needed)}" in out
    meta, _ = frontmatter.read(vault / "content" / "cod-open1.md")
    assert "estimate" not in meta  # nothing to say: the file is left as it was
