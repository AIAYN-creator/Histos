import os
import subprocess
import sys
from pathlib import Path

import pytest

from histos import canvas, frontmatter, validation
from histos.cli import build_parser

SRC = str(Path(__file__).resolve().parents[1] / "src")


def run(monkeypatch, tmp_path, *argv):
    monkeypatch.chdir(tmp_path)
    args = build_parser().parse_args(list(argv))
    return args.func(args)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    run(monkeypatch, tmp_path, "add-card", "cap3", "--title", "Tres")
    return tmp_path


def meta(vault, card_id="cap3"):
    return frontmatter.read(vault / "content" / f"{card_id}.md")[0]


def test_an_in_progress_card_cant_be_assigned_to_someone_else(vault, monkeypatch, capsys):
    assert run(monkeypatch, vault, "assign", "cap3", "--worker", "sub-cap3") == 0
    capsys.readouterr()
    assert run(monkeypatch, vault, "assign", "cap3", "--worker", "parent") == 1
    assert "already In progress (worker: sub-cap3)" in capsys.readouterr().err
    assert meta(vault)["worker"] == "sub-cap3"


def test_same_worker_again_is_a_no_op(vault, monkeypatch, capsys):
    run(monkeypatch, vault, "assign", "cap3", "--worker", "sub-cap3")
    before = (vault / "content" / "cap3.md").read_bytes()
    capsys.readouterr()
    assert run(monkeypatch, vault, "assign", "cap3", "--worker", "sub-cap3") == 0
    assert "already yours" in capsys.readouterr().out
    assert (vault / "content" / "cap3.md").read_bytes() == before


def test_takeover_says_from_whom(vault, monkeypatch, capsys):
    run(monkeypatch, vault, "assign", "cap3", "--worker", "sub-cap3")
    capsys.readouterr()
    assert run(monkeypatch, vault, "assign", "cap3", "--worker", "parent", "--takeover") == 0
    assert "taken over from sub-cap3" in capsys.readouterr().out
    assert meta(vault)["worker"] == "parent"


def test_without_workers_reassigning_now_needs_takeover(vault, monkeypatch, capsys):
    run(monkeypatch, vault, "assign", "cap3")
    assert "worker" not in meta(vault)
    capsys.readouterr()
    assert run(monkeypatch, vault, "assign", "cap3") == 1
    assert "no worker recorded" in capsys.readouterr().err
    assert run(monkeypatch, vault, "assign", "cap3", "--takeover") == 0


def test_reject_frees_the_card_and_approve_keeps_who_did_it(vault, monkeypatch):
    run(monkeypatch, vault, "add-card", "cap4", "--title", "Cuatro")
    draft = vault / "d.md"
    draft.write_text("Texto.\n", encoding="utf-8")
    for card_id in ("cap3", "cap4"):
        run(monkeypatch, vault, "assign", card_id, "--worker", "sub-1")
        run(monkeypatch, vault, "propose", card_id, "--file", str(draft))
    run(monkeypatch, vault, "reject", "cap3")
    run(monkeypatch, vault, "approve", "cap4")
    assert "worker" not in meta(vault, "cap3")
    assert meta(vault, "cap4")["worker"] == "sub-1"


def test_status_and_subagent_brief_show_the_worker(vault, monkeypatch, capsys):
    run(monkeypatch, vault, "assign", "cap3", "--worker", "sub-cap3")
    capsys.readouterr()
    run(monkeypatch, vault, "status")
    assert "  - cap3 [sub-cap3]  (content/cap3.md)" in capsys.readouterr().out
    run(monkeypatch, vault, "context", "cap3", "--for-subagent")
    assert "You are sub-cap3, working one Histos card" in capsys.readouterr().out


def test_validate_checks_the_worker_type(vault):
    md = vault / "content" / "cap3.md"
    m, body = frontmatter.read(md)
    m["worker"] = ["a", "b"]
    frontmatter.write(md, m, body)
    errors = validation.validate_frontmatter(vault, canvas.load(vault))
    assert any("worker" in e for e in errors)


def test_two_simultaneous_assigns_exactly_one_wins(vault):
    env = dict(os.environ, PYTHONPATH=SRC)
    procs = [
        subprocess.Popen([sys.executable, "-m", "histos", "assign", "cap3", "--worker", w],
                         cwd=vault, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for w in ("parent", "sub-cap3")
    ]
    codes = [p.wait(timeout=60) for p in procs]
    assert sorted(codes) == [0, 1]
    winner = ("parent", "sub-cap3")[codes.index(0)]
    assert meta(vault)["worker"] == winner
