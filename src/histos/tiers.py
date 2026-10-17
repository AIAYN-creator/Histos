"""Which tier a card gets, and why -- a pure function over an already validated config and what
heuristics.gather() measured, so it's testable without a vault.

The rules (decision dec-reglas):
1. Tiers are checked reasoning -> exec -> fast, whatever their order in the config.
2. A tier matches by its `match`: `any` (one condition holds) or `all` (every one does).
3. A tier with no conditions never matches.
4. The first tier that matches wins; if none does, `exec` by default.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import heuristics
from .config import Config

PRECEDENCE = ("reasoning", "exec", "fast")
DEFAULT_TIER = "exec"


@dataclass
class TierTrace:
    name: str
    match: str
    matched: bool
    details: list[str]  # what held (on a match) or what stopped it (otherwise)

    def render(self) -> str:
        verdict = "match" if self.matched else "no match"
        return f"{self.name} ({self.match}): {verdict} -- {'; '.join(self.details)}"


@dataclass
class Classification:
    tier: str
    by_default: bool
    traces: list[TierTrace] = field(default_factory=list)

    def render(self, card_id: str) -> str:
        head = f"{card_id} -> {self.tier}" + (" (default: no tier matched)" if self.by_default else "")
        return "\n".join([head] + [f"  {t.render()}" for t in self.traces])


def classify(cfg: Config, inputs: heuristics.Inputs) -> Classification:
    traces = []
    for name in PRECEDENCE:
        trace = _check_tier(cfg, name, inputs)
        traces.append(trace)
        if trace.matched:
            return Classification(tier=name, by_default=False, traces=traces)
    return Classification(tier=DEFAULT_TIER, by_default=True, traces=traces)


def _check_tier(cfg: Config, name: str, inputs: heuristics.Inputs) -> TierTrace:
    tier = cfg.tiers[name]
    if not tier.heuristics:
        return TierTrace(name, tier.match, False, ["no rules"])
    results = [(c, c.evaluate(inputs, cfg.terms)) for c in tier.heuristics]

    if tier.match == "all":
        failing = next(((c, o) for c, o in results if not o.holds), None)
        if failing is None:
            return TierTrace(name, "all", True, [o.detail for _, o in results])
        c, o = failing
        return TierTrace(name, "all", False, [f"{o.detail} fails '{c.source}'" if c.op else o.detail])

    held = [o.detail for _, o in results if o.holds]
    if held:
        return TierTrace(name, "any", True, held)
    return TierTrace(name, "any", False, [o.detail for _, o in results])
