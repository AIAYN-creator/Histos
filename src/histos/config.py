"""histos.config.yaml: per-vault tier heuristics and evals settings -- see docs/canvas-schema.md.

A vault without the file behaves exactly like v2 (load() returns None). An invalid file only
breaks what reads it (classify, evals on propose); status/diff/approve/reject never load it,
so a typo here can't keep the human from reviewing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path, PurePosixPath, PureWindowsPath

import yaml
from jsonschema import Draft202012Validator

from . import heuristics
from .errors import HistosError

CONFIG_FILENAME = "histos.config.yaml"
# Fixed by design (v3 principle 4): the config tunes each tier's rules, never which tiers exist.
TIERS = ("fast", "exec", "reasoning")
DEFAULT_MATCH = "any"
DEFAULT_EVALS_PATH = "evals/"

_SCHEMA_CACHE: dict | None = None


@dataclass
class Tier:
    name: str
    match: str
    heuristics: list[heuristics.Condition]


@dataclass
class EvalsConfig:
    enabled: bool
    path: str
    run_on: str


CRITICAL_PATH_COLOR = "#E83E8C"  # magenta: off Obsidian's six status presets


@dataclass
class CriticalPathConfig:
    enabled: bool = True
    color: str = CRITICAL_PATH_COLOR
    review: str = "active"  # or "calendar"


@dataclass
class NotificationsConfig:
    enabled: bool = True


@dataclass
class TokenPricingConfig:
    input_per_1m: float = 0.0
    output_per_1m: float = 0.0
    cache_write_per_1m: float = 0.0
    cache_read_per_1m: float = 0.0


@dataclass
class ExecutorConfig:
    name: str
    pricing_tier: str  # "free" | "paid"
    provider: str | None = None


@dataclass
class ExecutorsConfig:
    enabled: bool = False
    allowed: list[ExecutorConfig] = field(default_factory=list)

    def find(self, name: str) -> ExecutorConfig | None:
        target = name.strip()
        for entry in self.allowed:
            if entry.name == target:
                return entry
        return None


@dataclass
class Config:
    tiers: dict[str, Tier]
    evals: EvalsConfig
    terms: dict[str, list[str]]
    critical_path: CriticalPathConfig = None  # type: ignore[assignment]
    notifications: NotificationsConfig = None  # type: ignore[assignment]
    token_pricing: dict[str, TokenPricingConfig] = None  # type: ignore[assignment]
    token_logs: list[str] = None  # type: ignore[assignment]
    model_aliases: dict[str, str] = field(default_factory=dict)
    executors: ExecutorsConfig = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.critical_path is None:
            self.critical_path = CriticalPathConfig()
        if self.notifications is None:
            self.notifications = NotificationsConfig()
        if self.token_pricing is None:
            self.token_pricing = {}
        if self.token_logs is None:
            self.token_logs = []
        if self.model_aliases is None:
            self.model_aliases = {}
        if self.executors is None:
            self.executors = ExecutorsConfig()


def config_path(vault_root: Path) -> Path:
    return vault_root / CONFIG_FILENAME


def load(vault_root: Path) -> Config | None:
    path = config_path(vault_root)
    if not path.exists():
        return None
    raw, errors = _read_and_check(vault_root, path)
    if errors:
        raise HistosError(
            f"{CONFIG_FILENAME} is not valid ('histos validate' for detail):\n"
            + "\n".join(f"  - {e}" for e in errors)
        )
    return _build(raw)


def validate(vault_root: Path) -> list[str]:
    """Every problem with the vault's config, or [] if it's fine or doesn't exist."""
    path = config_path(vault_root)
    if not path.exists():
        return []
    return _read_and_check(vault_root, path)[1]


def _read_and_check(vault_root: Path, path: Path) -> tuple[dict, list[str]]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        return {}, [f"can't parse it as YAML: {e}"]
    if raw is None:
        return {}, ["the file is empty"]

    validator = Draft202012Validator(_load_schema())
    errors = []
    for e in validator.iter_errors(raw):
        where = "/".join(str(p) for p in e.path) or "(root)"
        errors.append(f"{where}: {e.message}")
    if errors:
        return raw, errors

    names = [t["name"] for t in raw["tiers"]]
    for name in TIERS:
        if names.count(name) != 1:
            errors.append(f"tiers: '{name}' must appear exactly once (found {names.count(name)})")

    for i, tier in enumerate(raw["tiers"]):
        for j, expr in enumerate(tier["heuristics"]):
            try:
                heuristics.parse(expr)
            except ValueError as e:
                errors.append(f"tiers/{i}/heuristics/{j}: {e}")

    evals_path = (raw.get("evals") or {}).get("path")
    if evals_path is not None and not _inside_vault(vault_root, evals_path):
        errors.append(f"evals/path: '{evals_path}' must be a relative path inside the vault")

    ex = raw.get("executors")
    if isinstance(ex, dict):
        allowed = ex.get("allowed") or []
        seen_names: set[str] = set()
        for idx, entry in enumerate(allowed):
            if not isinstance(entry, dict):
                continue
            name = entry.get("name", "")
            if isinstance(name, str):
                if name in seen_names:
                    errors.append(f"executors/allowed/{idx}/name: duplicate executor name '{name}'")
                seen_names.add(name)
        if ex.get("enabled") is True and not allowed:
            errors.append("executors: enabled is true but 'allowed' has no executors configured")
    return raw, errors


def _inside_vault(vault_root: Path, rel: str) -> bool:
    if PureWindowsPath(rel).is_absolute() or PureWindowsPath(rel).drive or PurePosixPath(rel).is_absolute():
        return False
    root = vault_root.resolve()
    target = (root / rel).resolve()
    return root in target.parents


def _build(raw: dict) -> Config:
    tiers = {
        t["name"]: Tier(
            name=t["name"],
            match=t.get("match", DEFAULT_MATCH),
            heuristics=[heuristics.parse(h) for h in t["heuristics"]],
        )
        for t in raw["tiers"]
    }
    ev = raw.get("evals")
    evals = EvalsConfig(
        enabled=bool(ev.get("enabled", True)) if ev is not None else False,
        path=(ev or {}).get("path", DEFAULT_EVALS_PATH),
        run_on=(ev or {}).get("run_on", "propose"),
    )
    cp = raw.get("critical_path") or {}
    critical_path = CriticalPathConfig(
        enabled=bool(cp.get("enabled", True)),
        color=cp.get("color", CRITICAL_PATH_COLOR).upper(),
        review=cp.get("review", "active"),
    )
    nt = raw.get("notifications") or {}
    notifications = NotificationsConfig(
        enabled=bool(nt.get("enabled", True)),
    )
    tp_raw = raw.get("token_pricing") or {}
    token_pricing = {
        str(model): TokenPricingConfig(
            input_per_1m=float(rates.get("input_per_1m", 0.0)),
            output_per_1m=float(rates.get("output_per_1m", 0.0)),
            cache_write_per_1m=float(rates.get("cache_write_per_1m", 0.0)),
            cache_read_per_1m=float(rates.get("cache_read_per_1m", 0.0)),
        )
        for model, rates in tp_raw.items()
    }
    token_logs = [str(p) for p in (raw.get("token_logs") or [])]
    raw_aliases = raw.get("model_aliases") or {}
    model_aliases = {
        str(k): str(v)
        for k, v in raw_aliases.items()
        if isinstance(k, str) and isinstance(v, str)
    }
    ex_raw = raw.get("executors") or {}
    executors = ExecutorsConfig(
        enabled=bool(ex_raw.get("enabled", False)),
        allowed=[
            ExecutorConfig(
                name=str(entry["name"]),
                pricing_tier=str(entry["pricing_tier"]),
                provider=str(entry["provider"]) if entry.get("provider") is not None else None,
            )
            for entry in (ex_raw.get("allowed") or [])
        ],
    )
    return Config(
        tiers=tiers,
        evals=evals,
        terms={k: list(v) for k, v in (raw.get("terms") or {}).items()},
        critical_path=critical_path,
        notifications=notifications,
        token_pricing=token_pricing,
        token_logs=token_logs,
        model_aliases=model_aliases,
        executors=executors,
    )


def _load_schema() -> dict:
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        ref = resources.files("histos").joinpath("schema", "histos-config.schema.json")
        _SCHEMA_CACHE = json.loads(ref.read_text(encoding="utf-8"))
    return _SCHEMA_CACHE
