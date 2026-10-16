import pytest

from histos import config
from histos.cli import build_parser
from histos.errors import HistosError

VALID = """\
tiers:
  - name: reasoning
    heuristics:
      - "architecture_terms_present"
      - "token_count >= 2000"
  - name: exec
    heuristics: ["code_blocks_present"]
  - name: fast
    match: all
    heuristics: ["token_count < 200", "no_code_blocks"]
evals:
  enabled: true
  path: evals/
  run_on: propose
"""


def write(tmp_path, text):
    (tmp_path / config.CONFIG_FILENAME).write_text(text, encoding="utf-8")


def run(monkeypatch, tmp_path, *argv):
    monkeypatch.chdir(tmp_path)
    args = build_parser().parse_args(list(argv))
    return args.func(args)


def test_no_file_means_v2_behaviour(tmp_path):
    assert config.load(tmp_path) is None
    assert config.validate(tmp_path) == []


def test_valid_file_loads_with_defaults(tmp_path):
    write(tmp_path, VALID)
    cfg = config.load(tmp_path)
    assert set(cfg.tiers) == {"fast", "exec", "reasoning"}
    assert cfg.tiers["reasoning"].match == "any"
    assert cfg.tiers["fast"].match == "all"
    assert [c.source for c in cfg.tiers["fast"].heuristics] == ["token_count < 200", "no_code_blocks"]
    assert cfg.terms == {}
    assert cfg.evals.enabled and cfg.evals.path == "evals/" and cfg.evals.run_on == "propose"


def test_missing_evals_section_means_evals_off(tmp_path):
    write(tmp_path, VALID.split("evals:")[0])
    assert config.load(tmp_path).evals.enabled is False


def test_evals_section_without_enabled_is_on(tmp_path):
    write(tmp_path, VALID.replace("  enabled: true\n", ""))
    assert config.load(tmp_path).evals.enabled is True


def test_extra_terms_load(tmp_path):
    write(tmp_path, VALID + "terms:\n  architecture: [ontologia]\n")
    assert config.load(tmp_path).terms == {"architecture": ["ontologia"]}


def test_a_tier_can_have_no_rules(tmp_path):
    write(tmp_path, VALID.replace('heuristics: ["code_blocks_present"]', "heuristics: []"))
    assert config.load(tmp_path).tiers["exec"].heuristics == []


@pytest.mark.parametrize("broken, fragment", [
    (VALID.replace("  - name: exec\n    heuristics: [\"code_blocks_present\"]\n", ""), "tiers"),
    (VALID.replace("name: exec", "name: fast"), "'exec' must appear exactly once"),
    (VALID.replace("name: exec", "name: medium"), "medium"),
    (VALID.replace("heuristics: [\"code_blocks_present\"]", "heuristic: [\"code_blocks_present\"]"), "heuristic"),
    (VALID.replace("match: all", "match: most"), "most"),
    (VALID.replace("path: evals/", "path: ../outside/"), "inside the vault"),
    (VALID.replace("path: evals/", "path: C:/evals/"), "inside the vault"),
    (VALID.replace("path: evals/", "path: /evals/"), "inside the vault"),
    (VALID.replace("path: evals/", "path: ."), "inside the vault"),
    (VALID.replace("run_on: propose", "run_on: approve"), "approve"),
    (VALID.replace('"architecture_terms_present"', '"arquitecture_terms_present"'), "unknown condition"),
    (VALID.replace('"token_count >= 2000"', '"token_count >= mucho"'), "unknown condition"),
    (VALID.replace('"token_count >= 2000"', '"token_count"'), "is a measure"),
    (VALID + "terms:\n  colors: [red]\n", "colors"),
    ("tiers: [unclosed\n", "YAML"),
    ("", "empty"),
])
def test_invalid_configs_are_reported(tmp_path, broken, fragment):
    write(tmp_path, broken)
    errors = config.validate(tmp_path)
    assert errors and any(fragment in e for e in errors), errors
    with pytest.raises(HistosError, match="histos.config.yaml is not valid"):
        config.load(tmp_path)


def test_validate_command_reports_config_errors(tmp_path, monkeypatch, capsys):
    assert run(monkeypatch, tmp_path, "init") == 0
    write(tmp_path, VALID.replace("match: all", "match: most"))
    capsys.readouterr()
    assert run(monkeypatch, tmp_path, "validate") == 1
    assert "config: " in capsys.readouterr().out


def test_invalid_config_does_not_block_reviewing(tmp_path, monkeypatch):
    assert run(monkeypatch, tmp_path, "init") == 0
    run(monkeypatch, tmp_path, "add-card", "cap1", "--title", "Uno")
    run(monkeypatch, tmp_path, "assign", "cap1")
    draft = tmp_path / "draft.md"
    draft.write_text("Texto.\n", encoding="utf-8")
    run(monkeypatch, tmp_path, "propose", "cap1", "--file", str(draft))
    write(tmp_path, "tiers: [unclosed\n")

    assert run(monkeypatch, tmp_path, "status") == 0
    assert run(monkeypatch, tmp_path, "approve", "cap1") == 0
