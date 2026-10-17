"""Hardening found in the v3 audit: a hand-edited canvas can't make Histos write outside the
vault, and broken card files give errors, never tracebacks."""
import json
import sys

import pytest
from conftest import run

from histos import canvas
from histos.errors import HistosError


@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    v.mkdir()
    run(monkeypatch, v, "init")
    run(monkeypatch, v, "add-card", "cap1", "--title", "Uno")
    return v


def point_card_to(vault, file_value):
    path = vault / "project.canvas"
    data = json.loads(path.read_text(encoding="utf-8"))
    for node in data["nodes"]:
        if node.get("id") == "cap1":
            node["file"] = file_value
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("file_value", [
    "content/..\\..\\escaped.md",   # a backslash is a separator on Windows
    "content/../escaped.md",
    "content/C:escaped.md",
    "content/sub/x.md",
])
def test_validate_refuses_a_card_file_outside_content(vault, monkeypatch, capsys, file_value):
    point_card_to(vault, file_value)
    capsys.readouterr()
    assert run(monkeypatch, vault, "validate") == 1
    assert "file" in capsys.readouterr().out


TRAVERSALS = ["content/../../escaped.md"]  # "/" separates folders everywhere
if sys.platform == "win32":
    TRAVERSALS.append("content/..\\..\\escaped.md")  # "\" only on Windows -- elsewhere it's part of a file name


@pytest.mark.parametrize("file_value", TRAVERSALS)
def test_approve_never_writes_outside_the_vault_even_past_the_schema(vault, file_value):
    outside = vault.parent / "escaped.md"
    outside.write_text("---\ndescription: x\n---\noriginal\n", encoding="utf-8")
    with pytest.raises(HistosError, match="outside content/"):
        canvas.card_file_path(vault, {"id": "cap1", "file": file_value})
    assert outside.read_text(encoding="utf-8").endswith("original\n")


def test_a_missing_card_file_is_an_error_not_a_traceback(vault, monkeypatch, capsys):
    (vault / "content" / "cap1.md").unlink()
    capsys.readouterr()
    assert run(monkeypatch, vault, "assign", "cap1") == 1
    assert "can't find" in capsys.readouterr().err
    assert run(monkeypatch, vault, "validate") == 1
    assert "doesn't exist" in capsys.readouterr().out


def test_a_frontmatter_that_isnt_a_mapping_is_an_error(vault, monkeypatch, capsys):
    (vault / "content" / "cap1.md").write_text("---\n- just\n- a list\n---\nbody\n", encoding="utf-8")
    capsys.readouterr()
    assert run(monkeypatch, vault, "assign", "cap1") == 1
    assert "key: value" in capsys.readouterr().err


def test_a_symlinked_lock_file_is_refused_not_followed(tmp_path, monkeypatch, capsys):
    import os
    vault = tmp_path / "vault"
    vault.mkdir()
    precious = tmp_path / "precious.txt"
    precious.write_text("precious data\n", encoding="utf-8")
    try:
        os.symlink(precious, vault / ".histos.lock")
    except OSError:
        pytest.skip("creating symlinks needs privileges here (Windows without developer mode)")
    capsys.readouterr()
    assert run(monkeypatch, vault, "init") == 1
    assert "symbolic link" in capsys.readouterr().err
    assert precious.read_text(encoding="utf-8") == "precious data\n"
