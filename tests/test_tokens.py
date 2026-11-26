"""Deterministic tests for local token/cost reading and status/report integration (`cod-token-cost`)."""
from __future__ import annotations

import dataclasses
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import run

from histos import canvas, config, events, metrics, operations, tokens


def _write_events(vault: Path, records: list[dict], *, vault_id: str = "tok-vault") -> None:
    log_dir = vault / events.LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / events.VAULT_ID_FILENAME).write_text(
        json.dumps({"id": vault_id, "created": "2026-11-25T09:00:00.000Z"}),
        encoding="utf-8",
    )
    (log_dir / events.LOG_FILENAME).write_text(
        "".join(json.dumps(r) + "\n" for r in records),
        encoding="utf-8",
    )


def _ev(ts: str, event: str, card: str | None = None, worker: str | None = None,
        data: dict | None = None, transitions: list[tuple] | None = None) -> dict:
    rec: dict = {"v": 1, "ts": ts, "event": event, "card": card, "actor": "agent", "worker": worker}
    if data:
        rec["data"] = data
    if transitions:
        rec["transitions"] = [{"card": c, "from": old, "to": new} for c, old, new in transitions]
    return rec


def _claude_line(ts: str, *, session_id: str | None, model: str | None,
                 inp: int = 0, out: int = 0, cw: int = 0, cr: int = 0,
                 prompt_text: str = "SECRET PROMPT SHOULD BE IGNORED") -> str:
    return json.dumps({
        "timestamp": ts,
        "sessionId": session_id,
        "type": "assistant",
        "message": {
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": prompt_text}],
            "usage": {
                "input_tokens": inp,
                "output_tokens": out,
                "cache_creation_input_tokens": cw,
                "cache_read_input_tokens": cr,
            },
        },
    })


def _setup_two_card_vault(vault: Path, monkeypatch) -> None:
    assert run(monkeypatch, vault, "init") == 0
    assert run(monkeypatch, vault, "add-card", "cod-1", "--title", "One", "--done-when", "Done 1.") == 0
    assert run(monkeypatch, vault, "add-card", "cod-2", "--title", "Two", "--done-when", "Done 2.") == 0


def test_sources_protocol_and_claude_code_parsing(tmp_path, monkeypatch):
    claude_dir = tmp_path / "claude"
    proj = claude_dir / "projects" / "my-proj"
    proj.mkdir(parents=True)
    transcript = proj / "sess-1.jsonl"
    transcript.write_text(
        "\n".join([
            "{broken json line",
            json.dumps({"timestamp": "2026-11-25T10:00:00Z", "type": "user", "message": {"role": "user", "content": "hi"}}),
            json.dumps({"timestamp": "not-a-date", "message": {"model": "claude-sonnet-4-5", "usage": {"input_tokens": 10}}}),
            _claude_line("2026-11-25T10:01:00.000Z", session_id="s1", model="claude-sonnet-4-5-20250929",
                         inp=1000, out=500, cw=200, cr=300),
            "",
        ]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(tokens.CLAUDE_DIR_ENV, str(claude_dir))

    src = tokens.ClaudeCodeSource()
    assert isinstance(src, tokens.TranscriptSource)
    records = src.read_records()
    assert len(records) == 1
    r = records[0]
    assert r == tokens.UsageRecord(
        ts=datetime(2026, 11, 25, 10, 1, 0, tzinfo=timezone.utc),
        provider="claude_code",
        model="claude-sonnet-4-5-20250929",
        session_id="s1",
        input_tokens=1000,
        output_tokens=500,
        cache_creation_tokens=200,
        cache_read_tokens=300,
    )
    assert not hasattr(r, "content") and not hasattr(r, "text")


def test_generic_jsonl_source_env_and_missing_paths(tmp_path, monkeypatch):
    log1 = tmp_path / "a.jsonl"
    log2_dir = tmp_path / "dir_logs"
    log2_dir.mkdir()
    log2 = log2_dir / "b.jsonl"
    log1.write_text(
        json.dumps({
            "ts": "2026-11-25T10:05:00Z",
            "provider": "codex",
            "model": "custom-fast",
            "session_id": "s-gen",
            "input_tokens": 2000,
            "output_tokens": 1000,
            "cache_write_tokens": 0,
            "cache_read_tokens": 500,
        }) + "\n{bad json\n",
        encoding="utf-8",
    )
    log2.write_text(
        json.dumps({
            "timestamp": "2026-11-25T10:06:00Z",
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }) + "\n",
        encoding="utf-8",
    )
    missing = tmp_path / "does-not-exist.jsonl"
    monkeypatch.setenv(tokens.TOKEN_LOGS_ENV, os.pathsep.join([str(log1), str(log2_dir), str(missing)]))

    src = tokens.GenericJsonlSource()
    assert isinstance(src, tokens.TranscriptSource)
    recs = src.read_records()
    assert len(recs) == 2
    assert recs[0].provider == "codex"
    assert recs[0].model == "custom-fast"
    assert recs[0].cache_read_tokens == 500
    assert recs[1].provider == "generic"
    assert recs[1].input_tokens == 100 and recs[1].output_tokens == 50


def test_window_attribution_worker_serialization_parallel_subagents_and_impl(tmp_path, monkeypatch):
    _setup_two_card_vault(tmp_path, monkeypatch)
    assert run(monkeypatch, tmp_path, "add-card", "cod-3", "--title", "Three", "--done-when", "Done 3.") == 0

    # cod-1 and cod-2 are batch-assigned to 'parent' at 10:00 (serialized by last_done);
    # cod-1 finishes at 10:10, cod-2 finishes at 10:20, then cod-2 has implementation 11:00-11:20.
    # cod-3 is worked by parallel subagent 'sub-3' from 10:05 to 10:25 (session s-sub3).
    # cod-1 also hits MAX_ATTEMPTS=3 at 10:04? Let's test retry gap on cod-1:
    # 10:00 assigned -> 10:04 proposal_refused(attempt=3) -> [human wait 10:04..10:07] -> 10:07 retry -> 10:10 proposed.
    _write_events(tmp_path, [
        _ev("2026-11-25T10:00:00Z", "assigned", "cod-1", "parent", transitions=[("cod-1", "Backlog", "In progress")]),
        _ev("2026-11-25T10:00:00Z", "assigned", "cod-2", "parent", transitions=[("cod-2", "Backlog", "In progress")]),
        _ev("2026-11-25T10:04:00Z", "proposal_refused", "cod-1", "parent", data={"attempt": 3}),
        _ev("2026-11-25T10:05:00Z", "assigned", "cod-3", "sub-3", transitions=[("cod-3", "Backlog", "In progress")]),
        _ev("2026-11-25T10:07:00Z", "retry", "cod-1", "parent"),
        _ev("2026-11-25T10:10:00Z", "proposed", "cod-1", "parent", transitions=[("cod-1", "In progress", "Proposal pending review")]),
        _ev("2026-11-25T10:20:00Z", "proposed", "cod-2", "parent", transitions=[("cod-2", "In progress", "Proposal pending review")]),
        _ev("2026-11-25T10:25:00Z", "proposed", "cod-3", "sub-3", transitions=[("cod-3", "In progress", "Proposal pending review")]),
        _ev("2026-11-25T10:30:00Z", "approved", "cod-2", None, transitions=[("cod-2", "Proposal pending review", "Approved")]),
        _ev("2026-11-25T11:00:00Z", "implementation_started", "cod-2", "parent"),
        _ev("2026-11-25T11:20:00Z", "implementation_done", "cod-2", "parent"),
    ])

    claude_dir = tmp_path / "claude" / "projects" / "p"
    claude_dir.mkdir(parents=True)
    (claude_dir / "run.jsonl").write_text(
        "\n".join([
            # Unrelated outside window (before 10:00) -> ignored
            _claude_line("2026-11-25T09:50:00Z", session_id="s-parent", model="claude-sonnet-4-5", inp=9999, out=9999),
            # cod-1 first attempt (10:02)
            _claude_line("2026-11-25T10:02:00Z", session_id="s-parent", model="claude-sonnet-4-5", inp=1000, out=200),
            # During human retry wait (10:05) on s-parent -> cod-1 closed at 10:04, cod-2 starts at 10:10 after cod-1;
            # only cod-3's window [10:05, 10:25] is open here, wait: use s-sub3 at 10:22 so s-parent at 10:05 isn't misattributed to cod-3!
            # Actually let's put human wait record at 10:04:30 (before sub-3 starts at 10:05) -> strictly unattributed!
            _claude_line("2026-11-25T10:04:30Z", session_id="s-parent", model="claude-sonnet-4-5", inp=5555, out=5555),
            # cod-1 after retry (10:08) overlaps cod-3 (10:05..10:25), disambiguated by s-parent (seen uniquely on cod-2/impl)
            _claude_line("2026-11-25T10:08:00Z", session_id="s-parent", model="claude-sonnet-4-5", inp=500, out=100),
            # cod-2 active after cod-1 closed at 10:10 -> 10:15 overlaps cod-3, disambiguated by session_id!
            _claude_line("2026-11-25T10:15:00Z", session_id="s-parent", model="claude-sonnet-4-5", inp=2000, out=400),
            # cod-2 implementation window (11:10) -> uniquely s-parent -> cod-2
            _claude_line("2026-11-25T11:10:00Z", session_id="s-parent", model="claude-sonnet-4-5", inp=3000, out=600),
            # cod-3 subagent records: 10:22 is unique to cod-3, 10:12 overlaps cod-2 -> disambiguated by s-sub3!
            _claude_line("2026-11-25T10:12:00Z", session_id="s-sub3", model="claude-haiku-4-5", inp=400, out=100),
            _claude_line("2026-11-25T10:22:00Z", session_id="s-sub3", model="claude-haiku-4-5", inp=600, out=200),
        ]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(tokens.CLAUDE_DIR_ENV, str(tmp_path / "claude"))
    monkeypatch.delenv(tokens.TOKEN_LOGS_ENV, raising=False)

    summary = tokens.summarize_vault(
        tmp_path,
        card_ids=["cod-1", "cod-2", "cod-3"],
        now=datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc),
    )
    assert summary.status == tokens.STATUS_OK
    assert summary.attributed_records == 6  # 09:50 and 10:04:30 excluded
    assert summary.cards["cod-1"].input_tokens == 1500
    assert summary.cards["cod-1"].output_tokens == 300
    assert summary.cards["cod-2"].input_tokens == 5000
    assert summary.cards["cod-2"].output_tokens == 1000
    assert summary.cards["cod-3"].input_tokens == 1000
    assert summary.cards["cod-3"].output_tokens == 300
    assert summary.total_tokens == (1500 + 300) + (5000 + 1000) + (1000 + 300)


def test_pricing_overrides_partial_pricing_and_config_schema(tmp_path, monkeypatch):
    _setup_two_card_vault(tmp_path, monkeypatch)
    cfg_path = config.config_path(tmp_path)
    base_cfg = cfg_path.read_text(encoding="utf-8")
    custom_log = tmp_path / "logs" / "custom.jsonl"
    custom_log.parent.mkdir(parents=True)
    cfg_path.write_text(
        base_cfg
        + "\ntoken_logs:\n  - logs/custom.jsonl\n"
        + "token_pricing:\n"
        + "  my-custom-model:\n"
        + "    input_per_1m: 10.0\n"
        + "    output_per_1m: 20.0\n"
        + "    cache_write_per_1m: 5.0\n"
        + "    cache_read_per_1m: 1.0\n",
        encoding="utf-8",
    )
    assert config.validate(tmp_path) == []
    loaded = config.load(tmp_path)
    assert loaded.token_logs == ["logs/custom.jsonl"]
    assert loaded.token_pricing["my-custom-model"].input_per_1m == 10.0

    # Invalid root key still rejected (additionalProperties: false)
    cfg_path.write_text(base_cfg + "\nunknown_section: true\n", encoding="utf-8")
    assert config.validate(tmp_path)

    # Restore valid config
    cfg_path.write_text(
        base_cfg
        + "\ntoken_logs:\n  - logs/custom.jsonl\n"
        + "token_pricing:\n"
        + "  my-custom-model:\n"
        + "    input_per_1m: 10.0\n"
        + "    output_per_1m: 20.0\n"
        + "    cache_write_per_1m: 5.0\n"
        + "    cache_read_per_1m: 1.0\n",
        encoding="utf-8",
    )

    _write_events(tmp_path, [
        _ev("2026-11-25T10:00:00Z", "assigned", "cod-1", "parent", transitions=[("cod-1", "Backlog", "In progress")]),
        _ev("2026-11-25T10:10:00Z", "proposed", "cod-1", "parent", transitions=[("cod-1", "In progress", "Proposal pending review")]),
        _ev("2026-11-25T10:20:00Z", "assigned", "cod-2", "parent", transitions=[("cod-2", "Backlog", "In progress")]),
        _ev("2026-11-25T10:30:00Z", "proposed", "cod-2", "parent", transitions=[("cod-2", "In progress", "Proposal pending review")]),
    ])
    custom_log.write_text(
        "\n".join([
            # cod-1: exact pricing from token_pricing: (100k*10 + 50k*20 + 20k*5 + 100k*1) / 1M = 1.0 + 1.0 + 0.1 + 0.1 = 2.2
            json.dumps({
                "ts": "2026-11-25T10:05:00Z",
                "model": "my-custom-model",
                "input_tokens": 100_000,
                "output_tokens": 50_000,
                "cache_write_tokens": 20_000,
                "cache_read_tokens": 100_000,
            }),
            # cod-2: unknown model -> exact token totals kept, cost_usd=None, status="partial_pricing"
            json.dumps({
                "ts": "2026-11-25T10:25:00Z",
                "model": "mystery-model-x",
                "input_tokens": 12_000,
                "output_tokens": 3_000,
            }),
        ]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(tokens.CLAUDE_DIR_ENV, "")
    monkeypatch.delenv(tokens.TOKEN_LOGS_ENV, raising=False)

    # First check cod-1 alone -> ok & exact cost 2.2
    summary_c1 = tokens.summarize_vault(tmp_path, card_ids=["cod-1"])
    assert summary_c1.status == tokens.STATUS_OK
    assert summary_c1.cost_usd == pytest.approx(2.2)
    assert summary_c1.cards["cod-1"].total_tokens == 270_000

    # Both cards -> cod-1 ok, cod-2 partial_pricing -> project partial_pricing & cost_usd=None
    summary_all = tokens.summarize_vault(tmp_path, card_ids=["cod-1", "cod-2"])
    assert summary_all.cards["cod-1"].status == tokens.STATUS_OK
    assert summary_all.cards["cod-1"].cost_usd == pytest.approx(2.2)
    assert summary_all.cards["cod-2"].status == tokens.STATUS_PARTIAL_PRICING
    assert summary_all.cards["cod-2"].cost_usd is None
    assert summary_all.cards["cod-2"].total_tokens == 15_000
    assert summary_all.status == tokens.STATUS_PARTIAL_PRICING
    assert summary_all.cost_usd is None
    assert summary_all.total_tokens == 285_000


def test_zero_transcripts_in_status_and_report(tmp_path, monkeypatch, capsys):
    _setup_two_card_vault(tmp_path, monkeypatch)
    monkeypatch.setenv(tokens.CLAUDE_DIR_ENV, str(tmp_path / "missing-claude"))
    monkeypatch.delenv(tokens.TOKEN_LOGS_ENV, raising=False)

    events_before = (tmp_path / ".histos" / "events.jsonl").read_text(encoding="utf-8")

    capsys.readouterr()
    assert run(monkeypatch, tmp_path, "status") == 0
    status_out = capsys.readouterr().out
    assert "Tokens: no data" in status_out

    assert run(monkeypatch, tmp_path, "report") == 0
    report_text = (tmp_path / "REPORT.md").read_text(encoding="utf-8")
    assert "No local transcript data found." in report_text
    assert "## 7. Tokens & Cost" in report_text
    assert (tmp_path / ".histos" / "events.jsonl").read_text(encoding="utf-8") == events_before


def test_status_and_report_integration_with_transcripts(tmp_path, monkeypatch, capsys):
    _setup_two_card_vault(tmp_path, monkeypatch)
    _write_events(tmp_path, [
        _ev("2026-11-25T10:00:00Z", "assigned", "cod-1", "parent", transitions=[("cod-1", "Backlog", "In progress")]),
        _ev("2026-11-25T10:15:00Z", "proposed", "cod-1", "parent", transitions=[("cod-1", "In progress", "Proposal pending review")]),
        _ev("2026-11-25T10:20:00Z", "approved", "cod-1", None, transitions=[("cod-1", "Proposal pending review", "Approved")]),
    ])
    # Update canvas color of cod-1 to Approved to match events
    data = canvas.load(tmp_path)
    canvas.find_card(data, "cod-1")["color"] = canvas.APPROVED
    canvas.save(tmp_path, data)

    claude_dir = tmp_path / "claude" / "projects" / "v"
    claude_dir.mkdir(parents=True)
    transcript = claude_dir / "s1.jsonl"
    raw_transcript = (
        _claude_line(
            "2026-11-25T10:05:00Z",
            session_id="s1",
            model="claude-sonnet-4-5",
            inp=100_000,
            out=20_000,
            cw=0,
            cr=0,
        )
        + "\n"
    )
    transcript.write_text(raw_transcript, encoding="utf-8")
    monkeypatch.setenv(tokens.CLAUDE_DIR_ENV, str(tmp_path / "claude"))
    monkeypatch.delenv(tokens.TOKEN_LOGS_ENV, raising=False)

    # Check StatusResult JSON serializability (GUI safety)
    st = operations.get_status(tmp_path)
    assert st.tokens is not None and st.tokens.status == tokens.STATUS_OK
    assert json.dumps(dataclasses.asdict(st))

    capsys.readouterr()
    assert run(monkeypatch, tmp_path, "status") == 0
    out = capsys.readouterr().out
    # 100k * 3/1M + 20k * 15/1M = 0.30 + 0.30 = $0.6000
    assert "120,000 tok · $0.6000" in out
    assert "Tokens: 120,000 (100,000 in · 20,000 out · 0 cache write · 0 cache read) · $0.6000" in out

    assert run(monkeypatch, tmp_path, "report") == 0
    report_text = (tmp_path / "REPORT.md").read_text(encoding="utf-8")
    assert "**Tokens:** 120,000 total (100,000 input, 20,000 output, 0 cache write, 0 cache read)" in report_text
    assert "**Token cost:** $0.6000" in report_text
    assert "## 7. Tokens & Cost" in report_text
    assert "| cod-1 | claude-sonnet-4-5 | 100,000 | 20,000 | 0 | 0 | 120,000 | $0.6000 | ok |" in report_text

    # Read-only guarantee: transcript file untouched
    assert transcript.read_text(encoding="utf-8") == raw_transcript
