"""The evals/ folder: deterministic checks run on a draft before it reaches the human.

Each .yaml/.yml file in the folder is a list of evals; each picks a check from the closed
catalog below and passes its parameters:

    - name: min_word_count
      check: word_count
      min: 300
      error: "At least 300 words"      # optional; each check has a default message
      only: ["cap*"]                   # optional: card id patterns it applies to
      except: ["cod-*"]                # optional

No expressions, no eval(): anything outside the catalog, an unknown parameter or one of the
wrong type is an error -- and an invalid evals/ folder blocks propose instead of being skipped
silently, because a filter that quietly switches itself off is worse than none.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

from . import checks
from .config import Config
from .errors import HistosError

_ID_PATTERN_RE = re.compile(r"^[A-Za-z0-9_\-*?]+$")
_COMMON_KEYS = {"name", "check", "error", "only", "except"}


def _non_negative_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _string_list(value) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, str) and v for v in value)


@dataclass
class CheckSpec:
    params: dict[str, Callable[[object], bool]]  # parameter -> type check
    param_types: dict[str, str]  # parameter -> human-readable type, for messages
    rule: Callable[[dict], str | None] = lambda params: None  # cross-parameter rule -> error


# The catalog: what each check accepts. checks.py implements what each one measures.
CATALOG: dict[str, CheckSpec] = {
    "word_count": CheckSpec(
        params={"min": _non_negative_int, "max": _non_negative_int},
        param_types={"min": "a whole number >= 0", "max": "a whole number >= 0"},
        rule=lambda p: "needs 'min', 'max' or both" if "min" not in p and "max" not in p
        else ("'min' is greater than 'max'" if "min" in p and "max" in p and p["min"] > p["max"] else None),
    ),
    "no_placeholders": CheckSpec(
        params={"patterns": _string_list},
        param_types={"patterns": "a non-empty list of texts"},
    ),
    "required_headings": CheckSpec(
        params={"headings": _string_list},
        param_types={"headings": "a non-empty list of headings"},
        rule=lambda p: "needs 'headings'" if "headings" not in p else None,
    ),
    "content_untouched": CheckSpec(params={}, param_types={}),
}


@dataclass
class Eval:
    name: str
    check: str
    params: dict
    error: str | None
    only: list[str] = field(default_factory=list)
    except_: list[str] = field(default_factory=list)
    file: str = ""

    def run(self, draft: str, vault_root: Path, data: dict, card: dict) -> checks.CheckOutcome:
        return checks.RUNNERS[self.check](checks.CheckInput(draft, self.params, vault_root, data, card))

    def applies_to(self, card_id: str) -> bool:
        if self.only and not any(fnmatch.fnmatchcase(card_id, p) for p in self.only):
            return False
        return not any(fnmatch.fnmatchcase(card_id, p) for p in self.except_)


def evals_dir(vault_root: Path, cfg: Config) -> Path:
    return vault_root / cfg.evals.path


def load(vault_root: Path, cfg: Config | None) -> list[Eval]:
    """The vault's evals, in file then list order -- [] when there's no config or evals are off."""
    evals, errors = _read_all(vault_root, cfg)
    if errors:
        raise HistosError(
            "the evals/ folder is not valid ('histos validate' for detail):\n"
            + "\n".join(f"  - {e}" for e in errors)
        )
    return evals


def validate(vault_root: Path, cfg: Config | None) -> list[str]:
    return _read_all(vault_root, cfg)[1]


def _read_all(vault_root: Path, cfg: Config | None) -> tuple[list[Eval], list[str]]:
    if cfg is None or not cfg.evals.enabled:
        return [], []
    folder = evals_dir(vault_root, cfg)
    if not folder.is_dir():
        return [], [f"the folder '{cfg.evals.path}' doesn't exist (evals.path in histos.config.yaml)"]

    evals: list[Eval] = []
    errors: list[str] = []
    seen: dict[str, str] = {}
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in (".yaml", ".yml"))
    for path in files:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            errors.append(f"{path.name}: can't parse it as YAML: {e}")
            continue
        if raw is None:
            continue  # an empty file is just an empty list
        if not isinstance(raw, list):
            errors.append(f"{path.name}: must be a list of evals (each one starting with '- name:')")
            continue
        for i, item in enumerate(raw):
            where = f"{path.name}: {i}"
            item_errors = _check_item(item)
            if item_errors:
                errors += [f"{where}: {e}" for e in item_errors]
                continue
            name = item["name"]
            if name in seen:
                errors.append(f"{where}: the name '{name}' is already used in {seen[name]}")
                continue
            seen[name] = path.name
            evals.append(Eval(
                name=name,
                check=item["check"],
                params={k: v for k, v in item.items() if k not in _COMMON_KEYS},
                error=item.get("error"),
                only=list(item.get("only") or []),
                except_=list(item.get("except") or []),
                file=path.name,
            ))
    return evals, errors


def _check_item(item) -> list[str]:
    if not isinstance(item, dict):
        return ["each eval must be a mapping with at least 'name' and 'check'"]
    errors = []
    if not isinstance(item.get("name"), str) or not item.get("name"):
        errors.append("'name' is required and must be text")
    check = item.get("check")
    if check not in CATALOG:
        errors.append(f"unknown check '{check}' (known: {', '.join(sorted(CATALOG))})")
        return errors
    if "error" in item and not isinstance(item["error"], str):
        errors.append("'error' must be text")
    for key in ("only", "except"):
        if key in item:
            patterns = item[key]
            if not _string_list(patterns):
                errors.append(f"'{key}' must be a non-empty list of card id patterns")
            else:
                errors += [f"'{key}': '{p}' is not a card id pattern" for p in patterns if not _ID_PATTERN_RE.match(p)]

    spec = CATALOG[check]
    params = {k: v for k, v in item.items() if k not in _COMMON_KEYS}
    for key, value in params.items():
        if key not in spec.params:
            allowed = ", ".join(spec.params) or "none"
            errors.append(f"'{check}' has no parameter '{key}' (allowed: {allowed})")
        elif not spec.params[key](value):
            errors.append(f"'{key}' must be {spec.param_types[key]}")
    if not errors:
        rule_error = spec.rule(params)
        if rule_error:
            errors.append(f"'{check}' {rule_error}")
    return errors
