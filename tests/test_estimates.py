"""estimates.py: types, groups, median and p80, and how durations are shown (cod-estimador).

Pure functions, so every case here is an invented list of samples."""
from datetime import datetime, timedelta, timezone

import pytest

from histos import estimates
from histos.estimates import ANY, Estimate, Missing, Row

T0 = datetime(2026, 12, 1, tzinfo=timezone.utc)


def sample(n=0, card="a", vault="v", tier="exec", deps=0, agent=1.0, review=1.0, active=None):
    from histos.history import Sample
    return Sample(vault=vault, card=card, approved_at=T0 + timedelta(hours=n), agent_hours=agent,
                  review_hours=review, review_active_hours=review if active is None else active,
                  rounds=1, rejections=0, tier=tier, model=None, dependencies=deps,
                  worker_kind="parent", approximate=False)


def rows(count, type_="cod", **kw):
    return [Row(sample(n=n, card=f"c{n}", **kw), type_) for n in range(count)]


# --- types ---

def test_a_vault_with_prefixes():
    assert estimates.card_types(["dec-a", "dec-b", "cod-x", "cod-y"]) == {
        "dec-a": "dec", "dec-b": "dec", "cod-x": "cod", "cod-y": "cod"}


def test_letters_before_the_numbers_are_the_prefix():
    types = estimates.card_types(["cap3", "cap4", "cap10"])
    assert set(types.values()) == {"cap"}


def test_a_vault_without_prefixes_has_no_types():
    assert estimates.card_types(["intro", "method", "results"]) == {"intro": None, "method": None, "results": None}
    assert estimates.card_types([]) == {}


def test_two_thirds_is_the_threshold():
    # 2 of 3 cards share a prefix: exactly two thirds, the vault uses prefixes
    assert estimates.card_types(["cap1", "cap2", "intro"]) == {"cap1": "cap", "cap2": "cap", "intro": "intro"}
    # 2 of 4: below the threshold
    assert set(estimates.card_types(["cap1", "cap2", "intro", "method"]).values()) == {None}


def test_samples_are_typed_with_their_own_vault():
    samples = [sample(card="cod-a", vault="one"), sample(card="cod-a", vault="two")]
    ids = {"one": ["cod-a", "cod-b"], "two": ["cod-a", "intro", "method"]}
    assert [r.type for r in estimates.typed(samples, ids)] == ["cod", None]


# --- groups ---

def test_agent_groups_go_from_specific_to_general():
    assert estimates.agent_groups("exec", "cod", 5) == [("exec", "cod", 2), ("exec", "cod", ANY), ("exec", ANY, ANY)]
    assert estimates.agent_groups("exec", None, 0) == [("exec", ANY, ANY)]


def test_review_groups():
    assert estimates.review_groups("dec") == [(ANY, "dec", ANY), (ANY, ANY, ANY)]
    assert estimates.review_groups(None) == [(ANY, ANY, ANY)]


def test_exactly_five_samples_in_the_specific_group_uses_it():
    data = rows(5, deps=1, agent=2.0) + rows(10, deps=0, agent=9.0)
    e = estimates.estimate(data, estimates.agent_groups("exec", "cod", 1), estimates.agent_value)
    assert isinstance(e, Estimate) and e.median == 2.0 and e.n == 5 and e.basis == "exec · cod · 1 deps · 5 cards"


def test_four_fall_down_to_the_next_group():
    data = rows(4, deps=1, agent=2.0) + rows(3, deps=0, agent=4.0)
    e = estimates.estimate(data, estimates.agent_groups("exec", "cod", 1), estimates.agent_value)
    assert e.basis == "exec · cod · 7 cards" and e.n == 7


def test_the_tier_group_is_the_last_resort():
    data = rows(2, type_="cod") + rows(3, type_="dec")
    e = estimates.estimate(data, estimates.agent_groups("exec", "cod", 0), estimates.agent_value)
    assert e.basis == "exec · 5 cards"


def test_no_group_gives_missing_with_what_the_closest_lacks():
    data = rows(2, deps=1) + rows(1, deps=0)
    m = estimates.estimate(data, estimates.agent_groups("exec", "cod", 1), estimates.agent_value)
    assert m == Missing(needed=2)  # the tier group has 3 of 5
    assert estimates.estimate([], estimates.agent_groups("exec", "cod", 1), estimates.agent_value) == Missing(5)


def test_a_different_tier_does_not_count():
    data = rows(6, tier="fast")
    assert estimates.estimate(data, estimates.agent_groups("exec", None, 0), estimates.agent_value) == Missing(5)


# --- median and p80 ---

def test_median_and_nearest_rank_p80():
    data = [Row(sample(n=n, card=f"c{n}", agent=v), None) for n, v in enumerate([5, 1, 4, 2, 3])]
    e = estimates.estimate(data, [(ANY, ANY, ANY)], estimates.agent_value)
    assert (e.median, e.p80) == (3, 4)  # ceil(0.8 * 5) = 4th smallest
    ten = [Row(sample(n=n, card=f"c{n}", agent=float(n + 1)), None) for n in range(10)]
    e = estimates.estimate(ten, [(ANY, ANY, ANY)], estimates.agent_value)
    assert (e.median, e.p80) == (5.5, 8.0)


def test_only_the_30_most_recent_count():
    old = [Row(sample(n=n, card=f"o{n}", agent=100.0), None) for n in range(20)]
    new = [Row(sample(n=100 + n, card=f"n{n}", agent=1.0), None) for n in range(30)]
    e = estimates.estimate(old + new, [(ANY, ANY, ANY)], estimates.agent_value)
    assert (e.median, e.p80, e.n) == (1.0, 1.0, 30) and e.basis == "all · 50 cards"


def test_review_uses_the_active_hours_not_the_calendar():
    data = [Row(sample(n=n, card=f"c{n}", review=24.0, active=2.0), None) for n in range(5)]
    e = estimates.estimate(data, estimates.review_groups(None), estimates.review_value)
    assert e.median == 2.0


# --- for_card ---

def test_for_card_gives_both_estimates():
    data = rows(5, agent=0.5, review=3.0)
    got = estimates.for_card(data, "exec", "cod", 0)
    assert got.agent.median == 0.5 and got.review.median == 3.0 and got.review.basis == "cod · 5 cards"


def test_an_unclassified_card_has_a_review_estimate_only():
    got = estimates.for_card(rows(5), None, "cod", 0)
    assert got.agent is estimates.UNCLASSIFIED and isinstance(got.review, Estimate)


# --- showing durations ---

@pytest.mark.parametrize("hours, text", [
    (0.001, "~5 min"), (4 / 60, "~5 min"), (25 / 60, "~25 min"), (55 / 60, "~55 min"), (58 / 60, "~1 h"),
    (1.0, "~1 h"), (2.87, "~2.9 h"), (3.02, "~3 h"), (9.5, "~9.5 h"), (9.96, "~10 h"), (12.4, "~12 h"),
    (23.0, "~23 h"), (23.7, "~1 d"), (24.0, "~1 d"), (60.0, "~2.5 d"),
])
def test_format_hours(hours, text):
    assert estimates.format_hours(hours) == text


def test_status_parts():
    e = estimates.CardEstimate(Estimate(25 / 60, 50 / 60, 7, "x"), Estimate(3.0, 24.0, 12, "y"))
    assert estimates.status_parts(e) == ["agent ~25 min (≤50 min)", "review ~3 h (≤1 d)"]
    e = estimates.CardEstimate(Missing(3), Estimate(5.0, 48.0, 12, "y"))
    assert estimates.status_parts(e) == ["agent no data (3 more needed)", "review ~5 h (≤2 d)"]
    e = estimates.CardEstimate(estimates.UNCLASSIFIED, Estimate(5.0, 48.0, 12, "y"))
    assert estimates.status_parts(e) == ["review ~5 h (≤2 d)"]
