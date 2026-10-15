import json
import os

import pytest

from histos import canvas, frontmatter, fsutil
from histos.errors import HistosError


def _leftover_temps(folder):
    return [p.name for p in folder.iterdir() if p.name.endswith(".tmp")]


def test_same_bytes_as_a_plain_write(tmp_path):
    text = "línea uno\nlínea dos ← flecha\n"
    plain = tmp_path / "plain.md"
    plain.write_text(text, encoding="utf-8")
    atomic = tmp_path / "atomic.md"
    fsutil.write_text_atomic(atomic, text)
    assert atomic.read_bytes() == plain.read_bytes()
    assert _leftover_temps(tmp_path) == []


def test_canvas_save_matches_the_old_json_dump(tmp_path):
    data = {"nodes": [{"id": "a", "type": "text", "text": "ñ", "x": 0, "y": 0, "width": 1, "height": 1}], "edges": []}
    canvas.save(tmp_path, data)
    old = tmp_path / "old.canvas"
    with old.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    assert canvas.vault_canvas_path(tmp_path).read_bytes() == old.read_bytes()


def test_a_crash_mid_write_leaves_the_original_intact(tmp_path, monkeypatch):
    target = tmp_path / "project.canvas"
    target.write_text("original\n", encoding="utf-8")
    before = target.read_bytes()

    def boom(fd):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(RuntimeError):
        fsutil.write_text_atomic(target, "new content\n")
    assert target.read_bytes() == before
    assert _leftover_temps(tmp_path) == []


def test_retries_while_another_program_holds_the_file(tmp_path, monkeypatch):
    target = tmp_path / "card.md"
    target.write_text("old\n", encoding="utf-8")
    real_replace = os.replace
    calls = []

    def flaky_replace(src, dst):
        calls.append(dst)
        if len(calls) < 3:
            raise PermissionError("file in use")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky_replace)
    monkeypatch.setattr(fsutil.time, "sleep", lambda s: None)
    fsutil.write_text_atomic(target, "new\n")
    assert len(calls) == 3
    assert target.read_text(encoding="utf-8") == "new\n"
    assert _leftover_temps(tmp_path) == []


def test_gives_up_with_a_clear_error_and_no_temp_left(tmp_path, monkeypatch):
    target = tmp_path / "project.canvas"
    target.write_text("original\n", encoding="utf-8")

    def always_busy(src, dst):
        raise PermissionError("file in use")

    monkeypatch.setattr(os, "replace", always_busy)
    monkeypatch.setattr(fsutil.time, "sleep", lambda s: None)
    with pytest.raises(HistosError, match="project.canvas"):
        fsutil.write_text_atomic(target, "new\n")
    assert target.read_text(encoding="utf-8") == "original\n"
    assert _leftover_temps(tmp_path) == []


def test_histos_error_is_still_reachable_from_canvas():
    assert canvas.HistosError is HistosError


def test_copy_is_byte_for_byte(tmp_path):
    src = tmp_path / "project.canvas"
    src.write_bytes(b'{\n  "nodes": []\n}\n')  # LF, as Obsidian writes it, even on Windows
    dst = tmp_path / "project.canvas.bak"
    fsutil.copy_file_atomic(src, dst)
    assert dst.read_bytes() == src.read_bytes()


def test_frontmatter_write_goes_through_the_atomic_path(tmp_path, monkeypatch):
    seen = []
    real = fsutil.write_text_atomic
    monkeypatch.setattr(frontmatter, "write_text_atomic", lambda p, t: (seen.append(p), real(p, t)))
    md = tmp_path / "cap1.md"
    frontmatter.write(md, {"description": "x"}, "body\n")
    assert seen == [md]
    assert frontmatter.read(md) == ({"description": "x"}, "body\n")
