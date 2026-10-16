import pytest

from histos import canvas, frontmatter, validation
from histos.cli import build_parser


def run(monkeypatch, tmp_path, *argv):
    monkeypatch.chdir(tmp_path)
    args = build_parser().parse_args(list(argv))
    return args.func(args)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    for card_id in ("cap3", "cap4", "cap7"):
        run(monkeypatch, tmp_path, "add-card", card_id, "--title", card_id)
    return tmp_path


def meta(vault, card_id="cap3"):
    return frontmatter.read(vault / "content" / f"{card_id}.md")[0]


def test_proposing_needs_no_authorization(vault, monkeypatch):
    assert run(monkeypatch, vault, "model", "cap3", "--proposed", "Sonnet 5.5") == 0
    assert meta(vault)["proposed_model"] == "Sonnet 5.5"


def test_approving_needs_authorized(vault, monkeypatch, capsys):
    assert run(monkeypatch, vault, "model", "cap3", "--approved", "Sonnet 5.5") == 1
    assert "needs explicit authorization" in capsys.readouterr().err
    assert "approved_model" not in meta(vault)


def test_approved_can_differ_and_both_are_kept(vault, monkeypatch, capsys):
    m = vault / "content" / "cap3.md"
    data, body = frontmatter.read(m)
    data["proposed_tier"] = "exec"
    frontmatter.write(m, data, body)
    run(monkeypatch, vault, "model", "cap3", "--proposed", "Sonnet 5.5")
    capsys.readouterr()
    assert run(monkeypatch, vault, "model", "cap3", "--approved", "Opus 5.5", "--authorized") == 0
    assert capsys.readouterr().out.strip() == "cap3: approved_model = Opus 5.5 (proposed: Sonnet 5.5, tier: exec)"
    assert (meta(vault)["proposed_model"], meta(vault)["approved_model"]) == ("Sonnet 5.5", "Opus 5.5")


def test_a_whole_queue_at_once(vault, monkeypatch):
    assert run(monkeypatch, vault, "model", "cap3", "cap4", "cap7", "--approved", "Sonnet 5.5", "--authorized") == 0
    assert all(meta(vault, c)["approved_model"] == "Sonnet 5.5" for c in ("cap3", "cap4", "cap7"))


def test_clear(vault, monkeypatch):
    run(monkeypatch, vault, "model", "cap3", "--proposed", "A")
    run(monkeypatch, vault, "model", "cap3", "--approved", "B", "--authorized")
    assert run(monkeypatch, vault, "model", "cap3", "--clear") == 0
    assert "proposed_model" not in meta(vault) and "approved_model" not in meta(vault)


def test_exactly_one_action(vault, monkeypatch):
    assert run(monkeypatch, vault, "model", "cap3") == 1
    assert run(monkeypatch, vault, "model", "cap3", "--proposed", "A", "--clear") == 1


def test_approved_cards_are_left_alone(vault, monkeypatch, capsys):
    draft = vault / "d.md"
    draft.write_text("x\n", encoding="utf-8")
    run(monkeypatch, vault, "assign", "cap3")
    run(monkeypatch, vault, "propose", "cap3", "--file", str(draft))
    run(monkeypatch, vault, "approve", "cap3")
    capsys.readouterr()
    assert run(monkeypatch, vault, "model", "cap3", "--proposed", "A") == 1
    assert "already Approved" in capsys.readouterr().err


def test_no_tier_warns_but_writes(vault, monkeypatch, capsys):
    capsys.readouterr()
    assert run(monkeypatch, vault, "model", "cap3", "--proposed", "A") == 0
    assert "has no proposed_tier" in capsys.readouterr().err


def test_fields_pass_validation(vault, monkeypatch):
    run(monkeypatch, vault, "model", "cap3", "--proposed", "A")
    run(monkeypatch, vault, "model", "cap3", "--approved", "B", "--authorized")
    assert validation.validate_frontmatter(vault, canvas.load(vault)) == []
