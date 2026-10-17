"""The README and docs/ never link to a file or heading that doesn't exist (scripts/check_links.py)."""
import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_links.py"
spec = importlib.util.spec_from_file_location("check_links", SCRIPT)
check_links = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = check_links
spec.loader.exec_module(check_links)


def test_every_relative_link_resolves():
    assert check_links.broken_links() == []


def test_github_anchor_rules():
    assert check_links.anchor("Loop 1 — Planning (the agent proposes)") == "loop-1--planning-the-agent-proposes"
    assert check_links.anchor("`.md` frontmatter") == "md-frontmatter"
    assert check_links.anchor("Color → status") == "color--status"


def test_a_broken_link_is_caught(tmp_path, monkeypatch):
    doc = tmp_path / "doc.md"
    doc.write_text("# Title\n\n[ok](#title) [gone](missing.md) [bad anchor](#nope)\n", encoding="utf-8")
    monkeypatch.setattr(check_links, "ROOT", tmp_path)
    monkeypatch.setattr(check_links, "markdown_files", lambda: [doc])
    assert check_links.broken_links() == ["doc.md: missing.md -> no such file", "doc.md: #nope -> no heading '#nope'"]
