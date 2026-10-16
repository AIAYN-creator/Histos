import pytest

from histos import config, tiers
from histos.heuristics import Inputs


def make_config(tmp_path, reasoning, exec_, fast, fast_match="all", terms=""):
    def rules(items):
        return "[" + ", ".join(f'"{r}"' for r in items) + "]"
    text = (
        "tiers:\n"
        f"  - name: fast\n    match: {fast_match}\n    heuristics: {rules(fast)}\n"
        f"  - name: exec\n    heuristics: {rules(exec_)}\n"
        f"  - name: reasoning\n    heuristics: {rules(reasoning)}\n"
        + terms
    )
    (tmp_path / config.CONFIG_FILENAME).write_text(text, encoding="utf-8")
    return config.load(tmp_path)


def text(card_text, full_text=None):
    return Inputs(card_text, card_text if full_text is None else full_text, 0, 0)


@pytest.fixture
def cfg(tmp_path):
    return make_config(
        tmp_path,
        reasoning=["debugging_terms_present", "architecture_terms_present"],
        exec_=["code_blocks_present"],
        fast=["token_count < 200", "no_code_blocks"],
    )


def test_precedence_reasoning_beats_exec_whatever_the_config_order(cfg):
    result = tiers.classify(cfg, text("Hay un traceback\n```\ncode\n```\n"))
    assert result.tier == "reasoning" and not result.by_default
    assert [t.name for t in result.traces] == ["reasoning"]  # lower tiers aren't even checked


def test_any_matches_on_one_condition(cfg):
    assert tiers.classify(cfg, text("```\ncode\n```\n" + "x" * 2000)).tier == "exec"


def test_all_needs_every_condition(cfg):
    assert tiers.classify(cfg, text("Corto.")).tier == "fast"
    long_plain = tiers.classify(cfg, text("palabra " * 200))
    assert long_plain.tier == "exec" and long_plain.by_default


def test_an_empty_tier_never_matches(tmp_path):
    cfg = make_config(tmp_path, reasoning=[], exec_=[], fast=[])
    result = tiers.classify(cfg, text("lo que sea"))
    assert result.tier == "exec" and result.by_default
    assert all(t.details == ["no rules"] for t in result.traces)


def test_the_reason_reads_like_the_decision(cfg):
    result = tiers.classify(cfg, text("```\nx\n```\n"))
    assert result.render("cap3") == (
        "cap3 -> exec\n"
        "  reasoning (any): no match -- no debugging terms; no architecture terms\n"
        "  exec (any): match -- fenced code block"
    )


def test_default_reason_names_what_blocked_fast(cfg):
    result = tiers.classify(cfg, text("x" * 2560))
    assert result.render("cap7") == (
        "cap7 -> exec (default: no tier matched)\n"
        "  reasoning (any): no match -- no debugging terms; no architecture terms\n"
        "  exec (any): no match -- no code blocks\n"
        "  fast (all): no match -- token_count = 640 fails 'token_count < 200'"
    )


def test_extra_terms_reach_the_conditions(tmp_path):
    cfg = make_config(tmp_path, reasoning=["architecture_terms_present"], exec_=[], fast=[],
                      terms="terms:\n  architecture: [ontologia]\n")
    assert tiers.classify(cfg, text("Definir la ontologia")).tier == "reasoning"


def test_the_spec_example_can_never_give_fast(tmp_path):
    # Documents a known issue (fixed in dec-defaults): exec has 'token_count < 2000' with any, and
    # is checked before fast, so everything fast could catch is caught by exec first.
    cfg = make_config(
        tmp_path,
        reasoning=["architecture_terms_present", "debugging_terms_present",
                   "multi_step_reasoning_implied", "token_count >= 2000"],
        exec_=["code_blocks_present", "file_operations_mentioned", "token_count < 2000"],
        fast=["token_count < 200", "no_code_blocks", "no_math_symbols"],
        fast_match="any",
    )
    assert tiers.classify(cfg, text("x" * 200)).tier == "exec"  # 50 tokens
