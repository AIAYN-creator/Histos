"""Tests for `histos doctor` (read-only vault health diagnostics)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from conftest import run

from histos import __version__, canvas, doctor, events, frontmatter


def _by_check(result: doctor.DoctorResult) -> dict[str, doctor.Finding]:
    return {f.check: f for f in result.findings}


def test_doctor_healthy_vault_passes_and_does_not_mutate(tmp_path, monkeypatch, capsys):
    assert run(monkeypatch, tmp_path, "init") == 0
    capsys.readouterr()

    events_before = (tmp_path / ".histos" / "events.jsonl").read_text(encoding="utf-8")
    lock_existed = (tmp_path / ".histos.lock").exists()

    rc = run(monkeypatch, tmp_path, "doctor")
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK (11 checks passed)" in out
    assert (tmp_path / ".histos" / "events.jsonl").read_text(encoding="utf-8") == events_before
    assert (tmp_path / ".histos.lock").exists() == lock_existed


def test_doctor_json_output(initialized_vault, monkeypatch, capsys):
    rc = run(monkeypatch, initialized_vault, "doctor", "--json")
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["counts"] == {"ok": 11, "warn": 0, "fail": 0}
    assert len(payload["findings"]) == 11


def test_doctor_missing_canvas_and_corrupt_canvas(tmp_path, monkeypatch):
    res_missing = doctor.diagnose(tmp_path)
    assert not res_missing.ok
    assert _by_check(res_missing)["vault_valid"].status == "fail"
    assert _by_check(res_missing)["vault_valid"].fix == "histos init"

    (tmp_path / canvas.CANVAS_FILENAME).write_text("{not valid json", encoding="utf-8")
    res_bad = doctor.diagnose(tmp_path)
    assert not res_bad.ok
    assert _by_check(res_bad)["vault_valid"].status == "fail"
    assert _by_check(res_bad)["vault_valid"].fix == "histos validate"


def test_doctor_missing_v3_config_and_agents_variants(initialized_vault, monkeypatch, capsys):
    (initialized_vault / "histos.config.yaml").unlink()
    (initialized_vault / "evals" / "guides.yaml").unlink()
    res = doctor.diagnose(initialized_vault)
    assert not res.ok
    assert _by_check(res)["v3_config"].status == "fail"
    assert _by_check(res)["v3_config"].fix == "histos init --add-config"

    agents_path = initialized_vault / "AGENTS.md"
    agents_path.write_text("# Old rules without stamp\n", encoding="utf-8")
    assert _by_check(doctor.diagnose(initialized_vault))["agents_md"].status == "warn"

    agents_path.write_text(f"# Rules\n<!-- histos-template-version: 0.0.1 -->\n", encoding="utf-8")
    finding = _by_check(doctor.diagnose(initialized_vault))["agents_md"]
    assert finding.status == "warn"
    assert "0.0.1" in finding.message and __version__ in finding.message

    agents_path.unlink()
    assert _by_check(doctor.diagnose(initialized_vault))["agents_md"].status == "fail"
    assert run(monkeypatch, initialized_vault, "doctor") == 1
    capsys.readouterr()


def test_doctor_legend_unclassified_done_when_brief_and_in_progress_model(initialized_vault, monkeypatch):
    assert run(monkeypatch, initialized_vault, "add-card", "cod-one", "--title", "One") == 0
    assert run(monkeypatch, initialized_vault, "assign", "cod-one", "--worker", "parent") == 0

    data = canvas.load(initialized_vault)
    for node in data["nodes"]:
        if node.get("id") == canvas.LEGEND_ID:
            node["text"] = "Custom modified legend"
    canvas.save(initialized_vault, data)

    by_check = _by_check(doctor.diagnose(initialized_vault))
    assert by_check["legend"].status == "warn"
    assert by_check["unclassified_cards"].status == "warn"
    assert "histos classify cod-one" == by_check["unclassified_cards"].fix
    assert by_check["in_progress_model"].status == "warn"
    assert "histos model cod-one --approved" in (by_check["in_progress_model"].fix or "")
    assert by_check["done_when_missing"].status == "warn"
    assert "histos accept-edit cod-one" in (by_check["done_when_missing"].fix or "")
    assert by_check["project_brief"].status == "warn"
    assert "PROJECT.md" in (by_check["project_brief"].fix or "")


def test_doctor_content_untouched_detects_direct_edit(initialized_vault, monkeypatch):
    assert run(monkeypatch, initialized_vault, "add-card", "cap1", "--title", "Cap 1", "--done-when", "Reviewed and approved.") == 0
    md_path = initialized_vault / "content" / "cap1.md"
    meta, body = frontmatter.read(md_path)
    frontmatter.write(md_path, meta, body + "\nEdited outside histos.\n")

    res = doctor.diagnose(initialized_vault)
    assert not res.ok
    finding = _by_check(res)["content_untouched"]
    assert finding.status == "fail"
    assert finding.fix == "histos accept-edit cap1"


def test_doctor_histos_home_and_pause_checks(initialized_vault, tmp_path, monkeypatch):
    assert run(monkeypatch, initialized_vault, "pause") == 0
    assert _by_check(doctor.diagnose(initialized_vault))["pause_active"].status == "warn"

    other_home = tmp_path / "other-home"
    other_home.mkdir()
    monkeypatch.setenv("HISTOS_HOME", str(other_home))
    home_finding = _by_check(doctor.diagnose(initialized_vault))["histos_home"]
    assert home_finding.status == "warn"
    assert home_finding.fix == "histos status"

    monkeypatch.setattr(doctor, "_is_writable_dir", lambda _p: False)
    unwritable = _by_check(doctor.diagnose(initialized_vault))["histos_home"]
    assert unwritable.status == "warn"
    assert "HISTOS_HOME" in (unwritable.fix or "")
