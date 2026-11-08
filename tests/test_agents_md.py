"""AGENTS.md (and the command reference) only name commands and flags that exist -- if a command is
renamed, the instructions every agent reads can't silently go stale (doc-entrevista)."""
import argparse
import re
from importlib import resources
from pathlib import Path

import pytest

from histos.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]
DOCS = {
    "AGENTS.md": resources.files("histos").joinpath("templates", "AGENTS.md").read_text(encoding="utf-8"),
    "docs/cli.md": (ROOT / "docs" / "cli.md").read_text(encoding="utf-8"),
}


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def _all_flags(parser: argparse.ArgumentParser) -> set[str]:
    flags = {opt for action in parser._actions for opt in action.option_strings}
    for sub in _subparsers(parser).values():
        flags |= _all_flags(sub)
    return flags


PARSER = build_parser()
COMMANDS = _subparsers(PARSER)
FLAGS = _all_flags(PARSER)


@pytest.mark.parametrize("name", DOCS)
def test_every_command_named_exists(name):
    for m in re.finditer(r"histos ([a-z][a-z-]*)(?: ([a-z][a-z-]*))?", DOCS[name]):
        command, action = m.group(1), m.group(2)
        assert command in COMMANDS, f"{name}: 'histos {command}' isn't a command"
        actions = _subparsers(COMMANDS[command])
        if actions and action:
            assert action in actions, f"{name}: 'histos {command} {action}' isn't a command"


@pytest.mark.parametrize("name", DOCS)
def test_every_flag_named_exists(name):
    named = set(re.findall(r"(?<![\w-])--[a-z][a-z-]*", DOCS[name]))
    assert named - FLAGS == set(), f"{name} names flags no command has"


def test_the_human_only_commands_are_listed_in_rule_5():
    rule5 = next(line for line in DOCS["AGENTS.md"].splitlines() if line.startswith("5. "))
    for command in ("`approve`", "`reject`", "`retry`", "`accept-edit`", "`guide approve`", "`guide reject`",
                    "`plan approve`", "`plan reject`"):
        assert command in rule5
