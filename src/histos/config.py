"""histos.config.yaml: per-vault tier heuristics and evals settings -- see docs/canvas-schema.md.

A vault without the file behaves exactly like v2 (load() returns None). An invalid file only
breaks what reads it (classify, evals on propose); status/diff/approve/reject never load it,
so a typo here can't keep the human from reviewing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
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


@dataclass
class Config:
    tiers: dict[str, Tier]
    evals: EvalsConfig
    terms: dict[str, list[str]]


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
    return Config(tiers=tiers, evals=evals, terms={k: list(v) for k, v in (raw.get("terms") or {}).items()})


def _load_schema() -> dict:
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        ref = resources.files("histos").joinpath("schema", "histos-config.schema.json")
        _SCHEMA_CACHE = json.loads(ref.read_text(encoding="utf-8"))
    return _SCHEMA_CACHE
