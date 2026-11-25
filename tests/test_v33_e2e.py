"""End-to-end tests for v3.3.0 (`histos report` + `histos archive`) over synthetic and sample vaults."""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

from conftest import run

from histos import canvas

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_make_history():
    spec = importlib.util.spec_from_file_location("make_history", REPO_ROOT / "scripts" / "make_history.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _markdown_section(text: str, heading: str) -> str:
    pattern = rf"(?ms)^{re.escape(heading)}\r?\n(.*?)(?=^## |\Z)"
    m = re.search(pattern, text)
    assert m is not None, f"missing section: {heading}"
    return m.group(1).strip()


def test_v33_e2e_synthetic_vault_report_and_archive(tmp_path, monkeypatch, capsys):
    make_history = _load_make_history()
    info = make_history.generate(tmp_path / "gen", cards=24, vaults=2, seed=7)
    monkeypatch.setenv("HISTOS_HOME", str(info.home))
    vault = info.vaults[0]

    assert run(monkeypatch, vault, "validate") == 0
    capsys.readouterr()

    # 1. Generate report before archiving and check all 6 sections
    assert run(monkeypatch, vault, "report", "--output", "REPORT_PRE.md") == 0
    capsys.readouterr()
    pre = (vault / "REPORT_PRE.md").read_text(encoding="utf-8")
    for section in (
        "## 1. Executive Summary",
        "## 2. Estimate Accuracy (Actual vs. Estimated)",
        "## 3. Rejections & Eval Attempts",
        "## 4. Critical Path Drift",
        "## 5. Timeline (Mermaid Gantt)",
        "## 6. Remaining Work (3-Point PERT)",
    ):
        assert section in pre
    assert "```mermaid\ngantt" in pre

    # 2. Dry-run archive leaves board untouched
    assert run(monkeypatch, vault, "archive", "--all-done", "--dry-run") == 0
    assert "dry run" in capsys.readouterr().out
    assert not (vault / "archive.canvas").exists()

    # 3. Real archive moves eligible Done cards cleanly and keeps project.canvas valid
    assert run(monkeypatch, vault, "archive", "--all-done") == 0
    capsys.readouterr()
    assert run(monkeypatch, vault, "validate") == 0
    capsys.readouterr()

    archive_path = vault / "archive.canvas"
    assert archive_path.exists()
    archive_data = json.loads(archive_path.read_text(encoding="utf-8"))
    archived_nodes = archive_data.get("nodes", [])
    assert len(archived_nodes) > 0
    for node in archived_nodes:
        assert node["type"] == "file"
        assert (vault / node["file"]).exists()

    archived_ids = {n["id"] for n in archived_nodes}
    remaining_ids = {c["id"] for c in canvas.cards(canvas.load(vault))}
    assert archived_ids.isdisjoint(remaining_ids)

    # 4. Report with --include-archived matches pre-archive historical totals and tables
    assert run(monkeypatch, vault, "report", "--include-archived", "--output", "REPORT_POST.md") == 0
    capsys.readouterr()
    post = (vault / "REPORT_POST.md").read_text(encoding="utf-8")

    assert _markdown_section(post, "## 1. Executive Summary") == _markdown_section(pre, "## 1. Executive Summary")
    assert _markdown_section(post, "## 3. Rejections & Eval Attempts") == _markdown_section(
        pre, "## 3. Rejections & Eval Attempts"
    )
    assert _markdown_section(post, "## 5. Timeline (Mermaid Gantt)") == _markdown_section(
        pre, "## 5. Timeline (Mermaid Gantt)"
    )


def test_v33_e2e_sample_paper_vault(tmp_path, monkeypatch, capsys):
    src = REPO_ROOT / "examples" / "sample-paper"
    dst = tmp_path / "sample-paper"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".histos.lock"))

    assert run(monkeypatch, dst, "validate") == 0
    capsys.readouterr()

    doc_rc = run(monkeypatch, dst, "doctor", "--json")
    assert doc_rc in (0, 1)
    doc_payload = json.loads(capsys.readouterr().out)
    assert len(doc_payload["findings"]) == 11
    assert any(f["check"] == "vault_valid" and f["status"] == "ok" for f in doc_payload["findings"])

    assert run(monkeypatch, dst, "report") == 0
    assert run(monkeypatch, dst, "archive", "--all-done", "--dry-run") == 0
    capsys.readouterr()

    report_text = (dst / "REPORT.md").read_text(encoding="utf-8")
    assert "## 1. Executive Summary" in report_text
    assert "## 5. Timeline (Mermaid Gantt)" in report_text
    assert "## 6. Remaining Work (3-Point PERT)" in report_text
