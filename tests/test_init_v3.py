import hashlib
import json
from importlib import resources

from histos import config, evals, operations
from histos.cli import build_parser


def run(monkeypatch, tmp_path, *argv):
    monkeypatch.chdir(tmp_path)
    args = build_parser().parse_args(list(argv))
    return args.func(args)


def template(rel):
    return resources.files("histos").joinpath("templates", *rel.split("/")).read_text(encoding="utf-8")


def test_init_creates_config_and_evals(tmp_path, monkeypatch):
    assert run(monkeypatch, tmp_path, "init") == 0
    assert (tmp_path / "histos.config.yaml").read_text(encoding="utf-8") == template("histos.config.yaml")
    assert (tmp_path / "evals" / "basics.yaml").exists() and (tmp_path / "evals" / "examples.yaml").exists()
    assert run(monkeypatch, tmp_path, "validate") == 0


def test_only_the_basic_evals_are_active(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    loaded = evals.load(tmp_path, config.load(tmp_path))
    assert [e.name for e in loaded] == ["no_direct_writes", "no_placeholders"]


def test_the_desktop_app_path_creates_the_same(tmp_path):
    operations.init_vault(tmp_path)
    assert (tmp_path / "histos.config.yaml").exists() and (tmp_path / "evals" / "basics.yaml").exists()


def _tree(vault):
    return {str(p.relative_to(vault)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in vault.rglob("*") if p.is_file()}


def _make_v2_vault(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    run(monkeypatch, tmp_path, "add-card", "cap1", "--title", "Uno")
    (tmp_path / "histos.config.yaml").unlink()
    for f in (tmp_path / "evals").iterdir():
        f.unlink()
    (tmp_path / "evals").rmdir()


def test_add_config_brings_a_v2_vault_up_and_touches_nothing_else(tmp_path, monkeypatch):
    _make_v2_vault(tmp_path, monkeypatch)
    before = _tree(tmp_path)
    assert run(monkeypatch, tmp_path, "init", "--add-config") == 0
    after = _tree(tmp_path)
    added = set(after) - set(before)
    assert {a.replace("\\", "/") for a in added} == {"histos.config.yaml", "evals/basics.yaml", "evals/examples.yaml"}
    changed = {k for k in before if before[k] != after[k]}
    assert {c.replace("\\", "/") for c in changed} <= {".claude/settings.json"}  # only the deny rules


def test_add_config_leaves_existing_files_alone(tmp_path, monkeypatch, capsys):
    run(monkeypatch, tmp_path, "init")
    (tmp_path / "histos.config.yaml").write_text("# mine\n" + template("histos.config.yaml"), encoding="utf-8")
    (tmp_path / "evals" / "basics.yaml").unlink()
    capsys.readouterr()
    assert run(monkeypatch, tmp_path, "init", "--add-config") == 0
    err = capsys.readouterr().err
    assert "histos.config.yaml already exists" in err and "evals/ already exists" in err
    assert (tmp_path / "histos.config.yaml").read_text(encoding="utf-8").startswith("# mine")
    assert not (tmp_path / "evals" / "basics.yaml").exists()  # never adds evals next to the human's


def test_add_config_needs_a_vault(tmp_path, monkeypatch, capsys):
    assert run(monkeypatch, tmp_path, "init", "--add-config") == 1
    assert "use plain 'histos init'" in capsys.readouterr().err


def test_the_agent_cant_edit_its_own_filter(tmp_path, monkeypatch):
    run(monkeypatch, tmp_path, "init")
    deny = json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))["permissions"]["deny"]
    for rule in ("Write(histos.config.yaml)", "Edit(histos.config.yaml)", "Write(evals/**)", "Edit(evals/**)"):
        assert rule in deny


def test_add_config_adds_the_deny_rules_to_an_older_vault(tmp_path, monkeypatch):
    _make_v2_vault(tmp_path, monkeypatch)
    settings = tmp_path / ".claude" / "settings.json"
    settings.write_text(json.dumps({"permissions": {"deny": ["Write(content/**)"]}}), encoding="utf-8")
    run(monkeypatch, tmp_path, "init", "--add-config")
    deny = json.loads(settings.read_text(encoding="utf-8"))["permissions"]["deny"]
    assert "Edit(evals/**)" in deny and "Write(content/**)" in deny
