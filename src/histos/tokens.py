"""Read-only local token usage and cost attribution from agent transcripts (`cod-token-cost`).

Reads local JSONL transcripts (Claude Code `~/.claude/projects/**/*.jsonl` or `HISTOS_CLAUDE_DIR`,
plus opt-in generic JSONL logs via `HISTOS_TOKEN_LOGS` or `token_logs` in `histos.config.yaml`),
extracting only timestamps, model identifiers, session ids, and token counts -- never prompt or
message text. Cross-references `.histos/events.jsonl` active working windows while respecting
worker session boundaries via `history.py`.
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable

from . import config, events, history
from .errors import HistosError

CLAUDE_DIR_ENV = "HISTOS_CLAUDE_DIR"
TOKEN_LOGS_ENV = "HISTOS_TOKEN_LOGS"

STATUS_OK = "ok"
STATUS_PARTIAL_PRICING = "partial_pricing"
STATUS_NO_TRANSCRIPTS = "no_transcripts"


@dataclass(frozen=True)
class UsageRecord:
    ts: datetime
    provider: str
    model: str | None
    session_id: str | None
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int


@dataclass(frozen=True)
class CardTokenUsage:
    card: str
    input_tokens: int
    output_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    total_tokens: int
    model: str | None
    cost_usd: float | None
    status: str  # "ok" | "partial_pricing" | "no_transcripts"


@dataclass(frozen=True)
class ModelPricing:
    input_per_1m: float
    output_per_1m: float
    cache_write_per_1m: float = 0.0
    cache_read_per_1m: float = 0.0


@dataclass(frozen=True)
class ProjectTokenSummary:
    cards: dict[str, CardTokenUsage]
    input_tokens: int
    output_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    total_tokens: int
    cost_usd: float | None
    status: str  # "ok" | "partial_pricing" | "no_transcripts"
    attributed_records: int
    total_records: int


@runtime_checkable
class TranscriptSource(Protocol):
    def read_records(self) -> list[UsageRecord]: ...


# Built-in USD rates per 1M tokens. Config `token_pricing` overrides these.
DEFAULT_PRICING: dict[str, ModelPricing] = {
    "claude-opus-4-6": ModelPricing(5.0, 25.0, 6.25, 0.50),
    "claude-opus-4-5": ModelPricing(5.0, 25.0, 6.25, 0.50),
    "claude-opus-4-1": ModelPricing(15.0, 75.0, 18.75, 1.50),
    "claude-opus-4": ModelPricing(15.0, 75.0, 18.75, 1.50),
    "claude-sonnet-4-6": ModelPricing(3.0, 15.0, 3.75, 0.30),
    "claude-sonnet-4-5": ModelPricing(3.0, 15.0, 3.75, 0.30),
    "claude-sonnet-4": ModelPricing(3.0, 15.0, 3.75, 0.30),
    "claude-3-7-sonnet": ModelPricing(3.0, 15.0, 3.75, 0.30),
    "claude-3-5-sonnet": ModelPricing(3.0, 15.0, 3.75, 0.30),
    "claude-haiku-4-5": ModelPricing(1.0, 5.0, 1.25, 0.10),
    "claude-3-5-haiku": ModelPricing(0.80, 4.0, 1.00, 0.08),
    "claude-3-haiku": ModelPricing(0.25, 1.25, 0.30, 0.03),
}

_DATE_SUFFIX_RE = re.compile(r"-(?:20\d{6}|latest)$")
_BEDROCK_SUFFIX_RE = re.compile(r"-v\d+:\d+$")
_PROVIDER_PREFIX_RE = re.compile(r"^(?:(?:us|eu|global)\.)?anthropic\.")


def _parse_ts(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _nonneg_int(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return max(0, value)


def _extract_usage_counts(usage: dict) -> tuple[int, int, int, int] | None:
    inp = _nonneg_int(usage.get("input_tokens"))
    out = _nonneg_int(usage.get("output_tokens"))
    cw = _nonneg_int(
        usage.get(
            "cache_creation_input_tokens",
            usage.get("cache_creation_tokens", usage.get("cache_write_tokens")),
        )
    )
    cr = _nonneg_int(usage.get("cache_read_input_tokens", usage.get("cache_read_tokens")))
    if inp is None and out is None and cw is None and cr is None:
        return None
    return (inp or 0, out or 0, cw or 0, cr or 0)


class ClaudeCodeSource:
    """Reads Claude Code JSONL transcripts from `~/.claude/projects/**/*.jsonl` or `HISTOS_CLAUDE_DIR`."""

    def __init__(self, claude_dir: Path | None = None) -> None:
        self._claude_dir = claude_dir

    def _jsonl_files(self) -> list[Path]:
        if self._claude_dir is not None:
            base = self._claude_dir.expanduser()
            target = base / "projects" if (base / "projects").is_dir() else base
            return sorted(target.rglob("*.jsonl")) if target.is_dir() else []

        if CLAUDE_DIR_ENV in os.environ:
            raw = os.environ[CLAUDE_DIR_ENV].strip()
            if not raw:
                return []
            base = Path(raw).expanduser()
            target = base / "projects" if (base / "projects").is_dir() else base
            return sorted(target.rglob("*.jsonl")) if target.is_dir() else []

        projects_dir = Path.home() / ".claude" / "projects"
        return sorted(projects_dir.rglob("*.jsonl")) if projects_dir.is_dir() else []

    def read_records(self) -> list[UsageRecord]:
        records: list[UsageRecord] = []
        for path in self._jsonl_files():
            try:
                with path.open("r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if not line.strip() or '"usage"' not in line:
                            continue
                        try:
                            obj = json.loads(line)
                        except ValueError:
                            continue
                        if not isinstance(obj, dict):
                            continue
                        ts = _parse_ts(obj.get("timestamp"))
                        if ts is None:
                            continue
                        msg = obj.get("message")
                        if not isinstance(msg, dict):
                            continue
                        usage = msg.get("usage")
                        if not isinstance(usage, dict):
                            continue
                        counts = _extract_usage_counts(usage)
                        if counts is None:
                            continue
                        raw_model = msg.get("model")
                        model = raw_model.strip() if isinstance(raw_model, str) and raw_model.strip() else None
                        raw_sid = obj.get("sessionId")
                        session_id = raw_sid.strip() if isinstance(raw_sid, str) and raw_sid.strip() else None
                        inp, out, cw, cr = counts
                        records.append(
                            UsageRecord(
                                ts=ts,
                                provider="claude_code",
                                model=model,
                                session_id=session_id,
                                input_tokens=inp,
                                output_tokens=out,
                                cache_creation_tokens=cw,
                                cache_read_tokens=cr,
                            )
                        )
            except OSError:
                continue
        return records


class GenericJsonlSource:
    """Reads opt-in local JSONL token logs from `HISTOS_TOKEN_LOGS` or explicit config paths."""

    def __init__(self, paths: Iterable[Path | str] | None = None, *, vault_root: Path | None = None) -> None:
        self._paths = list(paths) if paths is not None else None
        self._vault_root = vault_root

    def _resolve_path(self, item: Path | str) -> Path:
        p = Path(item).expanduser()
        if not p.is_absolute() and self._vault_root is not None:
            return self._vault_root / p
        return p

    def _jsonl_files(self) -> list[Path]:
        raw_items: list[Path | str] = []
        if self._paths is not None:
            raw_items.extend(self._paths)
        else:
            env_val = os.environ.get(TOKEN_LOGS_ENV, "")
            if env_val.strip():
                raw_items.extend(part for part in env_val.split(os.pathsep) if part.strip())

        files: list[Path] = []
        seen: set[Path] = set()
        for item in raw_items:
            p = self._resolve_path(item)
            if p.is_file():
                resolved = p.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    files.append(p)
            elif p.is_dir():
                for child in sorted(p.rglob("*.jsonl")):
                    resolved = child.resolve()
                    if resolved not in seen:
                        seen.add(resolved)
                        files.append(child)
        return files

    def read_records(self) -> list[UsageRecord]:
        records: list[UsageRecord] = []
        for path in self._jsonl_files():
            try:
                with path.open("r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if not line.strip():
                            continue
                        try:
                            obj = json.loads(line)
                        except ValueError:
                            continue
                        if not isinstance(obj, dict):
                            continue
                        ts = _parse_ts(obj.get("ts") if "ts" in obj else obj.get("timestamp"))
                        if ts is None:
                            continue
                        msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
                        usage = obj.get("usage")
                        if not isinstance(usage, dict):
                            usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else obj
                        counts = _extract_usage_counts(usage)
                        if counts is None:
                            continue
                        raw_provider = obj.get("provider")
                        provider = raw_provider.strip() if isinstance(raw_provider, str) and raw_provider.strip() else "generic"
                        raw_model = obj.get("model") if obj.get("model") is not None else msg.get("model")
                        model = raw_model.strip() if isinstance(raw_model, str) and raw_model.strip() else None
                        raw_sid = obj.get("session_id") if obj.get("session_id") is not None else obj.get("sessionId")
                        session_id = raw_sid.strip() if isinstance(raw_sid, str) and raw_sid.strip() else None
                        inp, out, cw, cr = counts
                        records.append(
                            UsageRecord(
                                ts=ts,
                                provider=provider,
                                model=model,
                                session_id=session_id,
                                input_tokens=inp,
                                output_tokens=out,
                                cache_creation_tokens=cw,
                                cache_read_tokens=cr,
                            )
                        )
            except OSError:
                continue
        return records


def _build_pricing_table(overrides: dict[str, config.TokenPricingConfig] | None) -> dict[str, ModelPricing]:
    table = dict(DEFAULT_PRICING)
    if overrides:
        for model_name, rate in overrides.items():
            key = model_name.strip().lower()
            if not key:
                continue
            table[key] = ModelPricing(
                input_per_1m=float(rate.input_per_1m),
                output_per_1m=float(rate.output_per_1m),
                cache_write_per_1m=float(rate.cache_write_per_1m),
                cache_read_per_1m=float(rate.cache_read_per_1m),
            )
    return table


def lookup_pricing(model: str | None, table: dict[str, ModelPricing]) -> ModelPricing | None:
    if not model:
        return None
    norm = model.strip().lower()
    if not norm:
        return None
    if norm in table:
        return table[norm]
    stripped = _PROVIDER_PREFIX_RE.sub("", norm)
    stripped = _BEDROCK_SUFFIX_RE.sub("", stripped)
    if stripped in table:
        return table[stripped]
    no_date = _DATE_SUFFIX_RE.sub("", stripped)
    return table.get(no_date)


def record_cost_usd(record: UsageRecord, table: dict[str, ModelPricing]) -> float | None:
    pricing = lookup_pricing(record.model, table)
    if pricing is None:
        return None
    return (
        record.input_tokens * pricing.input_per_1m
        + record.output_tokens * pricing.output_per_1m
        + record.cache_creation_tokens * pricing.cache_write_per_1m
        + record.cache_read_tokens * pricing.cache_read_per_1m
    ) / 1_000_000.0


@dataclass(frozen=True)
class _WorkingWindow:
    card: str
    start: datetime
    end: datetime
    worker: str | None


@dataclass
class _CardWindowState:
    worker: str | None = None
    agent_open: datetime | None = None
    implement_open: datetime | None = None
    state: str | None = None


def extract_working_windows(
    log_events: list[dict],
    *,
    now: datetime | None = None,
) -> list[_WorkingWindow]:
    """Reconstructs per-card active agent/implementation working spans from `events.jsonl`.

    Mirrors `history.py`'s worker serialization rule: a single worker works one card at a time,
    while distinct workers (e.g. `parent` vs `sub-*`) may overlap in parallel.
    """
    cutoff = now or datetime.now(timezone.utc)
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    else:
        cutoff = cutoff.astimezone(timezone.utc)

    states: dict[str, _CardWindowState] = {}
    last_done: dict[str | None, tuple[datetime, str]] = {}
    windows: list[_WorkingWindow] = []

    def state_of(cid: str) -> _CardWindowState:
        return states.setdefault(cid, _CardWindowState())

    def close_agent(cid: str, st: _CardWindowState, ts: datetime) -> None:
        if st.agent_open is None:
            return
        start = st.agent_open
        done = last_done.get(st.worker)
        if done and done[1] != cid and done[0] > start:
            start = min(done[0], ts)
        if ts >= start:
            windows.append(_WorkingWindow(card=cid, start=start, end=ts, worker=st.worker))
        st.agent_open = None
        last_done[st.worker] = (ts, cid)

    for e in log_events:
        ts = _parse_ts(e.get("ts"))
        if ts is None:
            continue
        name = e.get("event")
        card_id = e.get("card")
        data = e.get("data") or {}
        if not isinstance(data, dict):
            data = {}

        if isinstance(card_id, str) and card_id:
            st = state_of(card_id)
            if name == "assigned":
                if data.get("takeover") and st.agent_open is not None:
                    close_agent(card_id, st, ts)
                    st.agent_open = ts
                elif st.agent_open is None:
                    st.agent_open = ts
                st.worker = e.get("worker")
            elif name == "proposed":
                if st.agent_open is not None:
                    close_agent(card_id, st, ts)
            elif name == "proposal_refused":
                if st.agent_open is not None:
                    close_agent(card_id, st, ts)
                    if data.get("attempt") != history.MAX_ATTEMPTS:
                        st.agent_open = ts
            elif name == "retry":
                if st.agent_open is None:
                    st.agent_open = ts
            elif name == "implementation_started":
                st.implement_open = st.implement_open or ts
                if e.get("worker") is not None:
                    st.worker = e.get("worker")
            elif name == "implementation_done" and st.implement_open is not None:
                if ts >= st.implement_open:
                    windows.append(_WorkingWindow(card=card_id, start=st.implement_open, end=ts, worker=st.worker))
                st.implement_open = None

        for t in e.get("transitions") or []:
            if not isinstance(t, dict):
                continue
            tcid = t.get("card")
            if not isinstance(tcid, str) or not tcid:
                continue
            old, new = t.get("from"), t.get("to")
            st = state_of(tcid)
            if new == history._IN_PROGRESS and old != history._IN_PROGRESS:
                if st.agent_open is None:
                    st.agent_open = ts
            if old == history._IN_PROGRESS and new != history._IN_PROGRESS:
                close_agent(tcid, st, ts)
            st.state = new

    for cid, st in states.items():
        if st.agent_open is not None and cutoff >= st.agent_open:
            close_agent(cid, st, cutoff)
        if st.implement_open is not None and cutoff >= st.implement_open:
            windows.append(_WorkingWindow(card=cid, start=st.implement_open, end=cutoff, worker=st.worker))

    return windows


def _pick_window(
    record: UsageRecord,
    candidates: list[_WorkingWindow],
    session_cards: dict[str, set[str]],
) -> _WorkingWindow:
    if len(candidates) == 1:
        return candidates[0]

    if record.session_id and record.session_id in session_cards:
        known = session_cards[record.session_id]
        matched = [w for w in candidates if w.card in known]
        if len(matched) == 1:
            return matched[0]
        if matched:
            candidates = matched

    # At a boundary timestamp shared between a closing window and a starting window, prefer the closing one.
    ending_now = [w for w in candidates if w.end == record.ts and w.start < record.ts]
    if len(ending_now) == 1:
        return ending_now[0]

    return min(candidates, key=lambda w: ((w.end - w.start).total_seconds(), w.start, w.card))


def attribute_records(
    records: list[UsageRecord],
    windows: list[_WorkingWindow],
    *,
    card_ids: Iterable[str] | None = None,
    pricing_overrides: dict[str, config.TokenPricingConfig] | None = None,
) -> ProjectTokenSummary:
    """Attributes `records` to active working `windows` and computes per-card and project totals."""
    table = _build_pricing_table(pricing_overrides)
    target_cards = sorted(set(card_ids)) if card_ids is not None else sorted({w.card for w in windows})
    target_set = set(target_cards)

    active_windows = [w for w in windows if w.card in target_set] if card_ids is not None else list(windows)

    # Pass 1: find candidate windows and learn unambiguous session -> card affinity.
    matches_per_record: list[list[_WorkingWindow]] = []
    session_cards: dict[str, set[str]] = defaultdict(set)
    for rec in records:
        candidates = [w for w in active_windows if w.start <= rec.ts <= w.end]
        matches_per_record.append(candidates)
        if len(candidates) == 1 and rec.session_id:
            session_cards[rec.session_id].add(candidates[0].card)

    # Pass 2: assign each record to at most one card.
    by_card: dict[str, list[UsageRecord]] = {cid: [] for cid in target_cards}
    attributed_records = 0
    for rec, candidates in zip(records, matches_per_record):
        if not candidates:
            continue
        chosen = _pick_window(rec, candidates, session_cards)
        by_card.setdefault(chosen.card, []).append(rec)
        attributed_records += 1

    card_usages: dict[str, CardTokenUsage] = {}
    proj_in = proj_out = proj_cw = proj_cr = 0
    proj_cost = 0.0
    proj_partial = False

    for cid in sorted(by_card):
        recs = by_card[cid]
        if not recs:
            card_usages[cid] = CardTokenUsage(
                card=cid,
                input_tokens=0,
                output_tokens=0,
                cache_write_tokens=0,
                cache_read_tokens=0,
                total_tokens=0,
                model=None,
                cost_usd=None,
                status=STATUS_NO_TRANSCRIPTS,
            )
            continue

        c_in = sum(r.input_tokens for r in recs)
        c_out = sum(r.output_tokens for r in recs)
        c_cw = sum(r.cache_creation_tokens for r in recs)
        c_cr = sum(r.cache_read_tokens for r in recs)
        c_total = c_in + c_out + c_cw + c_cr

        model_tokens: dict[str, int] = defaultdict(int)
        c_cost = 0.0
        unknown_price = False
        for r in recs:
            if r.model:
                model_tokens[r.model] += (
                    r.input_tokens + r.output_tokens + r.cache_creation_tokens + r.cache_read_tokens
                )
            rcost = record_cost_usd(r, table)
            if rcost is None:
                unknown_price = True
            else:
                c_cost += rcost

        dominant_model = (
            max(model_tokens.items(), key=lambda item: (item[1], item[0]))[0]
            if model_tokens
            else None
        )
        if unknown_price:
            card_cost = None
            card_status = STATUS_PARTIAL_PRICING
            proj_partial = True
        else:
            card_cost = round(c_cost, 6)
            card_status = STATUS_OK
            proj_cost += c_cost

        card_usages[cid] = CardTokenUsage(
            card=cid,
            input_tokens=c_in,
            output_tokens=c_out,
            cache_write_tokens=c_cw,
            cache_read_tokens=c_cr,
            total_tokens=c_total,
            model=dominant_model,
            cost_usd=card_cost,
            status=card_status,
        )
        proj_in += c_in
        proj_out += c_out
        proj_cw += c_cw
        proj_cr += c_cr

    proj_total = proj_in + proj_out + proj_cw + proj_cr
    if attributed_records == 0:
        proj_status = STATUS_NO_TRANSCRIPTS
        final_cost = None
    elif proj_partial:
        proj_status = STATUS_PARTIAL_PRICING
        final_cost = None
    else:
        proj_status = STATUS_OK
        final_cost = round(proj_cost, 6)

    return ProjectTokenSummary(
        cards=card_usages,
        input_tokens=proj_in,
        output_tokens=proj_out,
        cache_write_tokens=proj_cw,
        cache_read_tokens=proj_cr,
        total_tokens=proj_total,
        cost_usd=final_cost,
        status=proj_status,
        attributed_records=attributed_records,
        total_records=len(records),
    )


def default_sources(vault_root: Path, cfg: config.Config | None) -> list[TranscriptSource]:
    sources: list[TranscriptSource] = [ClaudeCodeSource()]
    env_logs = os.environ.get(TOKEN_LOGS_ENV, "").strip()
    if env_logs:
        sources.append(GenericJsonlSource(vault_root=vault_root))
    if cfg is not None and cfg.token_logs:
        sources.append(GenericJsonlSource(cfg.token_logs, vault_root=vault_root))
    return sources


def summarize_vault(
    vault_root: Path,
    *,
    card_ids: Iterable[str] | None = None,
    log_events: list[dict] | None = None,
    sources: Iterable[TranscriptSource] | None = None,
    now: datetime | None = None,
) -> ProjectTokenSummary:
    """Read-only token and cost summary for `vault_root`. Never raises on missing transcripts or bad config."""
    try:
        cfg = config.load(vault_root)
    except (HistosError, OSError, ValueError):
        cfg = None

    if log_events is None:
        try:
            log_events = events.read(vault_root)
        except (OSError, ValueError):
            log_events = []

    windows = extract_working_windows(log_events, now=now)
    if not windows:
        return attribute_records(
            [],
            [],
            card_ids=card_ids,
            pricing_overrides=cfg.token_pricing if cfg is not None else None,
        )

    active_sources = list(sources) if sources is not None else default_sources(vault_root, cfg)
    records: list[UsageRecord] = []
    for src in active_sources:
        try:
            records.extend(src.read_records())
        except OSError:
            continue

    return attribute_records(
        records,
        windows,
        card_ids=card_ids,
        pricing_overrides=cfg.token_pricing if cfg is not None else None,
    )


def format_token_count(count: int) -> str:
    return f"{count:,}"


def format_cost(cost_usd: float | None) -> str:
    if cost_usd is None:
        return "cost partial"
    return f"${cost_usd:.4f}"
