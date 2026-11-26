"""Markdown project report generator (`histos report`) built on `histos.metrics` (`dec-report-spec`,
`cod-report-cmd`).

Writes a single report file atomically inside the vault without taking `.histos.lock` or mutating
the board or event log.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import canvas, config, estimates, events, metrics, tokens
from .errors import HistosError
from .fsutil import write_text_atomic

DEFAULT_REPORT_NAME = "REPORT.md"
_PROTECTED_FILES = {
    canvas.CANVAS_FILENAME.lower(),
    "project.md",
    "architecture.md",
    config.CONFIG_FILENAME.lower(),
    "agents.md",
    "claude.md",
}
_PROTECTED_DIRS = {"content", "evals", "proposals", "approved", "propuestas", "aprobados", ".histos", ".claude"}


@dataclass(frozen=True)
class GanttTask:
    card: str
    section: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class ReportResult:
    output: Path
    metrics: metrics.ProjectMetrics
    gantt_tasks: int


def _resolve_output(vault_root: Path, output: str | None) -> Path:
    name = (DEFAULT_REPORT_NAME if output is None else output).strip()
    if not name:
        raise HistosError("report output path can't be empty")
    if Path(name).is_absolute():
        raise HistosError(f"report output must be relative to the vault: '{name}'")
    root_resolved = vault_root.resolve()
    target = (root_resolved / name).resolve()
    try:
        rel = target.relative_to(root_resolved)
    except ValueError:
        raise HistosError(f"report output escapes the vault: '{name}'") from None
    parts = [p.lower() for p in rel.parts]
    if not parts:
        raise HistosError("report output must name a file")
    if len(parts) == 1 and parts[0] in _PROTECTED_FILES:
        raise HistosError(f"refusing to overwrite protected vault file '{rel.as_posix()}'")
    if parts[0] in _PROTECTED_DIRS:
        raise HistosError(f"refusing to write report inside protected folder '{parts[0]}/'")
    if target.is_symlink():
        raise HistosError(f"report output '{name}' is a symbolic link")
    return target


def _parse_ts(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def extract_gantt_tasks(log_events: list[dict], card_ids: set[str] | None = None) -> list[GanttTask]:
    """Derives one Gantt bar per approved card from `events.jsonl` timestamps."""
    started: dict[str, datetime] = {}
    approved: dict[str, datetime] = {}

    for e in log_events:
        ts_raw = e.get("ts")
        if not isinstance(ts_raw, str):
            continue
        ts = _parse_ts(ts_raw)
        cid = e.get("card")
        ev = e.get("event")
        if cid and ev in ("assigned", "card_created") and cid not in started:
            started[cid] = ts
        for t in e.get("transitions") or []:
            tcid = t.get("card")
            if not tcid:
                continue
            if t.get("to") == "In progress" and tcid not in started:
                started[tcid] = ts
            if t.get("to") == "Approved":
                approved[tcid] = ts
        if cid and ev == "approved" and cid not in approved:
            approved[cid] = ts

    tasks: list[GanttTask] = []
    for cid, end_dt in sorted(approved.items(), key=lambda item: (item[1], item[0])):
        if card_ids is not None and cid not in card_ids:
            continue
        start_dt = started.get(cid, end_dt)
        if end_dt <= start_dt:
            end_dt = start_dt + timedelta(minutes=1)
        prefix = estimates._prefix(cid) or "cards"
        tasks.append(GanttTask(card=cid, section=prefix, start=start_dt, end=end_dt))
    return tasks


def _fmt(val: float | None, suffix: str = " h") -> str:
    if val is None:
        return "no data"
    return f"{val:.2f}{suffix}"


def _fmt_pct(val: float | None) -> str:
    if val is None:
        return "no data"
    return f"{val:.1f}%"


def render_markdown(
    m: metrics.ProjectMetrics,
    gantt_tasks: list[GanttTask],
    *,
    generated_at: datetime | None = None,
) -> str:
    now = (generated_at or datetime.now(timezone.utc)).astimezone().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# Project Report — {m.vault_name}",
        "",
        f"Generated: {now}",
        "",
        "## 1. Executive Summary",
        "",
        f"- **Cards:** {m.total_cards} total ({m.approved_cards} approved, {m.open_cards} open)",
        f"- **Agent time:** {m.total_agent_hours:.2f} h",
        f"- **Active review time:** {m.total_review_active_hours:.2f} h",
        f"- **Calendar review time:** {m.total_review_calendar_hours:.2f} h",
    ]
    tok = m.tokens
    if tok is not None and tok.status != tokens.STATUS_NO_TRANSCRIPTS:
        lines += [
            f"- **Tokens:** {tok.total_tokens:,} total ({tok.input_tokens:,} input, {tok.output_tokens:,} output, "
            f"{tok.cache_write_tokens:,} cache write, {tok.cache_read_tokens:,} cache read)",
            f"- **Token cost:** {tokens.format_cost(tok.cost_usd)}",
        ]
    else:
        lines.append("- **Tokens & cost:** No local transcript data found.")
    lines += [
        "",
        "## 2. Estimate Accuracy (Actual vs. Estimated)",
        "",
        f"- **Compared cards:** {m.accuracy.compared}",
        f"- **Median absolute error:** {_fmt(m.accuracy.median_error_hours)}",
        f"- **Within p80 bound:** {_fmt_pct(m.accuracy.pct_within_p80)}",
        "",
    ]

    if m.accuracy.cards:
        lines += [
            "| Card | Estimated | Actual | Error | Ratio | Within p80 |",
            "|---|---|---|---|---|---|",
        ]
        for c in m.accuracy.cards:
            p80 = "yes" if c.within_p80 is True else ("no" if c.within_p80 is False else "no data")
            ratio = f"{c.ratio:.2f}x" if c.ratio is not None else "no data"
            lines.append(
                f"| {c.card} | {_fmt(c.estimated_hours)} | {_fmt(c.actual_hours)} | "
                f"{_fmt(c.error_hours)} | {ratio} | {p80} |"
            )
    else:
        lines.append("No closed cards with accuracy data yet.")

    lines += ["", "## 3. Rejections & Eval Attempts", ""]
    if m.by_tier:
        lines += [
            "### By Tier",
            "",
            "| Tier | Approved | Rejections | Rejection rate | Avg eval attempts |",
            "|---|---|---|---|---|",
        ]
        for g in m.by_tier:
            rate = _fmt_pct(g.rejection_rate * 100) if g.rejection_rate is not None else "no data"
            att = f"{g.avg_attempts:.2f}" if g.avg_attempts is not None else "no data"
            lines.append(f"| {g.key} | {g.approved} | {g.rejections} | {rate} | {att} |")
        lines.append("")
    else:
        lines += ["No tier statistics yet.", ""]

    if m.by_model:
        lines += [
            "### By Model",
            "",
            "| Model | Approved | Rejections | Rejection rate | Avg eval attempts |",
            "|---|---|---|---|---|",
        ]
        for g in m.by_model:
            rate = _fmt_pct(g.rejection_rate * 100) if g.rejection_rate is not None else "no data"
            att = f"{g.avg_attempts:.2f}" if g.avg_attempts is not None else "no data"
            lines.append(f"| {g.key} | {g.approved} | {g.rejections} | {rate} | {att} |")
        lines.append("")
    else:
        lines += ["No model statistics yet.", ""]

    if m.by_executor:
        lines += [
            "### By Executor",
            "",
            "| Executor | Approved | Rejections | Rejection rate | Avg eval attempts |",
            "|---|---|---|---|---|",
        ]
        for g in m.by_executor:
            rate = _fmt_pct(g.rejection_rate * 100) if g.rejection_rate is not None else "no data"
            att = f"{g.avg_attempts:.2f}" if g.avg_attempts is not None else "no data"
            lines.append(f"| {g.key} | {g.approved} | {g.rejections} | {rate} | {att} |")
        lines.append("")
    else:
        lines += ["### By Executor", "", "no data", ""]

    lines += [
        "## 4. Critical Path Drift",
        "",
        f"- **Initial critical chain:** {' -> '.join(m.drift.initial) if m.drift.initial else 'none'}",
        f"- **Remaining critical chain:** {' -> '.join(m.drift.final) if m.drift.final else 'none (all done)'}",
        f"- **Added to critical chain:** {', '.join(m.drift.added) if m.drift.added else 'none'}",
        f"- **Removed / completed:** {', '.join(m.drift.removed) if m.drift.removed else 'none'}",
        f"- **Slipped outside initial chain:** {', '.join(m.drift.slipped_outside_initial) if m.drift.slipped_outside_initial else 'none'}",
        "",
        "## 5. Timeline (Mermaid Gantt)",
        "",
    ]

    if gantt_tasks:
        lines += [
            "```mermaid",
            "gantt",
            f"    title {m.vault_name} Timeline",
            "    dateFormat YYYY-MM-DD HH:mm",
        ]
        by_section: dict[str, list[GanttTask]] = {}
        for t in gantt_tasks:
            by_section.setdefault(t.section, []).append(t)
        for section, tasks in by_section.items():
            lines.append(f"    section {section}")
            for t in tasks:
                s = t.start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
                e = t.end.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
                lines.append(f"    {t.card} :done, {t.card}, {s}, {e}")
        lines.append("```")
    else:
        lines.append("No approved cards with timeline timestamps yet.")

    lines += ["", "## 6. Remaining Work (3-Point PERT)", ""]
    if m.pert_open:
        lines += [
            "| Card | Optimistic (p20) | Most likely (p50) | Pessimistic (p80) | Expected (E) | Std dev (SD) |",
            "|---|---|---|---|---|---|",
        ]
        for p in m.pert_open:
            lines.append(
                f"| {p.card} | {_fmt(p.optimistic)} | {_fmt(p.most_likely)} | "
                f"{_fmt(p.pessimistic)} | {_fmt(p.expected)} | {_fmt(p.std_dev)} |"
            )
        lines.append("")
        if m.pert_critical_expected is not None and m.pert_critical_sd is not None:
            lines.append(
                f"- **Remaining critical path PERT:** {m.pert_critical_expected:.2f} h "
                f"(± {m.pert_critical_sd:.2f} h SD)"
            )
        else:
            lines.append("- **Remaining critical path PERT:** no data (insufficient closed history)")
    else:
        lines.append("All cards are approved — no remaining PERT work.")

    lines += ["", "## 7. Tokens & Cost", ""]
    if tok is not None and tok.status != tokens.STATUS_NO_TRANSCRIPTS:
        lines += [
            "| Card | Model | Input | Output | Cache Write | Cache Read | Total | Cost (USD) | Status |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for cid in sorted(tok.cards):
            c = tok.cards[cid]
            if c.status == tokens.STATUS_NO_TRANSCRIPTS:
                continue
            cost_str = f"${c.cost_usd:.4f}" if c.cost_usd is not None else "partial (no pricing)"
            model_str = c.model or "unknown"
            lines.append(
                f"| {c.card} | {model_str} | {c.input_tokens:,} | {c.output_tokens:,} | "
                f"{c.cache_write_tokens:,} | {c.cache_read_tokens:,} | {c.total_tokens:,} | "
                f"{cost_str} | {c.status} |"
            )
    else:
        lines.append("No local transcript data found.")

    return "\n".join(lines).rstrip() + "\n"


def write_report(vault_root: Path, *, output: str | None = None, include_archived: bool = False) -> ReportResult:
    target = _resolve_output(vault_root, output)
    proj_metrics = metrics.from_vault(vault_root, include_archived=include_archived)
    data = canvas.load(vault_root)
    if include_archived:
        data = metrics._merge_archived_canvas(vault_root, data)
    card_ids = {c["id"] for c in canvas.cards(data)}
    tasks = extract_gantt_tasks(events.read(vault_root), card_ids=card_ids)
    text = render_markdown(proj_metrics, tasks)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(target, text)
    return ReportResult(output=target, metrics=proj_metrics, gantt_tasks=len(tasks))
