"""How long an open card will take, from the cards like it that were already closed (dec-estimador).

Pure functions: nothing here reads or writes the disk -- everything comes in through the
arguments, so it's tested with invented samples. Descriptive statistics only, deterministic and
explainable: the median is "the usual", the 80th percentile (nearest rank, no interpolation) is
"if it goes badly". The groups and MIN_SAMPLES live only here, so `--coverage` can reuse them.

Two estimates per card, each with its own groups:

- agent time depends on the card: (tier, type, dependencies 0/1/2+), then (tier, type), then (tier);
- review time depends on the human: (type), then every card.

The review figure is built from `Sample.review_active_hours` -- the hours the human really spent
(calendar time minus their `histos pause` periods) -- not from the calendar `review_hours`.
"""
from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass

from .history import Sample

MIN_SAMPLES = 5
RECENT = 30
ANY = "*"  # a group position that matches everything

Group = tuple  # (tier, type, dependencies), each position a value or ANY


@dataclass(frozen=True)
class Row:
    """A closed card's sample with its type, the traits groups are made of."""
    sample: Sample
    type: str | None

    @property
    def key(self) -> tuple:
        return (self.sample.tier, self.type, dependency_bucket(self.sample.dependencies))


@dataclass(frozen=True)
class Estimate:
    median: float
    p80: float
    n: int
    basis: str  # the group used, e.g. "exec · cod · 7 cards"


@dataclass(frozen=True)
class Missing:
    needed: int  # samples the closest group still lacks


@dataclass(frozen=True)
class Unclassified:
    """The card has no tier yet, so there is no agent estimate."""


UNCLASSIFIED = Unclassified()


@dataclass(frozen=True)
class CardEstimate:
    agent: Estimate | Missing | Unclassified
    review: Estimate | Missing


def dependency_bucket(count: int) -> int:
    return min(count, 2)  # 0, 1 or 2+


# --- Types ---------------------------------------------------------------------------------

_USES_PREFIXES = (2, 3)  # at least two thirds of a vault's cards must share a prefix


def _prefix(card_id: str) -> str | None:
    """What goes before the first '-' (dec-tiempos -> dec) or the letters before the numbers
    (cap3 -> cap)."""
    if "-" in card_id:
        return card_id.split("-", 1)[0] or None
    m = re.match(r"[^\W\d_]+", card_id)
    return m.group(0) if m else None


def card_types(card_ids: list[str]) -> dict[str, str | None]:
    """The type of each card of ONE vault. It only counts if the vault really uses prefixes: at
    least two thirds of its cards share a prefix with another one. Otherwise every type is None."""
    prefixes = {card_id: _prefix(card_id) for card_id in card_ids}
    counts: dict[str, int] = {}
    for p in prefixes.values():
        if p:
            counts[p] = counts.get(p, 0) + 1
    sharing = sum(1 for p in prefixes.values() if p and counts[p] > 1)
    num, den = _USES_PREFIXES
    if not card_ids or sharing * den < num * len(card_ids):
        return {card_id: None for card_id in card_ids}
    return prefixes


def typed(samples: list[Sample], card_ids_by_vault: dict[str, list[str]]) -> list[Row]:
    """Each sample with its type, read with the card ids of ITS vault (a `cod` in one vault and
    a `cod` in another are the same type; a vault without prefixes gives None)."""
    types = {vault: card_types(ids) for vault, ids in card_ids_by_vault.items()}
    return [Row(s, types.get(s.vault, {}).get(s.card)) for s in samples]


# --- Groups --------------------------------------------------------------------------------

def agent_groups(tier: str, type_: str | None, dependencies: int) -> list[Group]:
    """From the most specific to the most general. Without a type only the tier group applies."""
    groups: list[Group] = []
    if type_:
        groups += [(tier, type_, dependency_bucket(dependencies)), (tier, type_, ANY)]
    groups.append((tier, ANY, ANY))
    return groups


def review_groups(type_: str | None) -> list[Group]:
    return ([(ANY, type_, ANY)] if type_ else []) + [(ANY, ANY, ANY)]


def _matches(group: Group, key: tuple) -> bool:
    return all(g == ANY or g == k for g, k in zip(group, key))


def basis_text(group: Group, n: int) -> str:
    tier, type_, deps = group
    parts = [tier] if tier != ANY else []
    if type_ != ANY:
        parts.append(type_)
    if deps != ANY:
        parts.append(f"{deps}{'+' if deps == 2 else ''} deps")
    if not parts:
        parts.append("all")
    return " · ".join(parts + [f"{n} cards"])


# --- Numbers -------------------------------------------------------------------------------

def percentile80(values: list[float]) -> float:
    """Nearest rank: the smallest value with at least 80% of the data at or below it."""
    ordered = sorted(values)
    return ordered[math.ceil(0.8 * len(ordered)) - 1]


def estimate(rows: list[Row], groups: list[Group], value) -> Estimate | Missing:
    """The first group with MIN_SAMPLES rows, from its RECENT most recent ones. `value` picks the
    number out of a sample. If no group qualifies: how many the closest one lacks."""
    best = 0
    for group in groups:
        members = [r for r in rows if _matches(group, r.key)]
        if len(members) >= MIN_SAMPLES:
            recent = sorted(members, key=lambda r: r.sample.approved_at)[-RECENT:]
            numbers = [value(r.sample) for r in recent]
            return Estimate(median=statistics.median(numbers), p80=percentile80(numbers),
                            n=len(recent), basis=basis_text(group, len(members)))
        best = max(best, len(members))
    return Missing(needed=MIN_SAMPLES - best)


def agent_value(sample: Sample) -> float:
    return sample.agent_hours


def review_value(sample: Sample) -> float:
    # The human's real effort: calendar review time minus the hours they were away.
    return sample.review_active_hours


def for_card(rows: list[Row], tier: str | None, type_: str | None, dependencies: int) -> CardEstimate:
    agent = (estimate(rows, agent_groups(tier, type_, dependencies), agent_value)
             if tier else UNCLASSIFIED)
    return CardEstimate(agent=agent, review=estimate(rows, review_groups(type_), review_value))


# --- Showing durations ---------------------------------------------------------------------

def format_hours(hours: float) -> str:
    """Rounded on purpose ("~3 h", not "2.87 h"): a false precision misleads. Under 1 h, minutes
    to the nearest 5 (at least "~5 min"); up to 24 h, hours (one decimal only below 10); from
    24 h, days with one decimal."""
    minutes = max(5, round(hours * 12) * 5)  # round(hours * 60 / 5) * 5
    if hours < 1 and minutes < 60:
        return f"~{minutes} min"
    if hours < 10:
        rounded = round(hours, 1)
    else:
        rounded = round(hours)
    if rounded < 24:
        return f"~{_plain(rounded)} h"
    return f"~{_plain(round(hours / 24, 1))} d"


def _plain(number: float) -> str:
    return str(int(number)) if number == int(number) else str(number)


def format_estimate(name: str, e: Estimate | Missing | Unclassified) -> str | None:
    if isinstance(e, Unclassified):
        return None
    if isinstance(e, Missing):
        return f"{name} no data ({e.needed} more needed)"
    return f"{name} {format_hours(e.median)} (≤{format_hours(e.p80)[1:]})"


def status_parts(e: CardEstimate) -> list[str]:
    """['agent ~25 min (≤50 min)', 'review ~3 h (≤1 d)'] -- the agent part is left out for an
    unclassified card (status says 'unclassified' itself)."""
    return [p for p in (format_estimate("agent", e.agent), format_estimate("review", e.review)) if p]
