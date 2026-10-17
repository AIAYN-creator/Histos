"""Histos CLI. Each subcommand is a cmd_xxx(args) -> int, testable without going through argparse."""
from __future__ import annotations

import argparse
import sys

from . import __version__, context
from .commands._shared import _permission_path  # noqa: F401  (tests and the docs refer to it here)
from .commands.board import (
    cmd_add_card,
    cmd_arrange,
    cmd_assign,
    cmd_describe,
    cmd_init,
    cmd_link,
    cmd_status,
    cmd_validate,
)
from .commands.review import (
    cmd_accept_edit,
    cmd_approve,
    cmd_diff,
    cmd_eval,
    cmd_propose,
    cmd_reject,
    cmd_retry,
)
from .commands.routing import cmd_classify, cmd_context, cmd_guide, cmd_model


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="histos", description="Histos CLI (see docs/canvas-schema.md)")
    parser.add_argument("--version", action="version", version=f"histos {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="creates project.canvas + content/ in the current directory")
    p.add_argument(
        "--add-config", action="store_true",
        help="existing vault: only add histos.config.yaml and evals/ if they're missing, touch nothing else",
    )
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("add-card", help="creates a new card")
    p.add_argument("id")
    p.add_argument("--title", required=True)
    p.add_argument("--description", default=None, help="one line, goes into the frontmatter and the initial body")
    p.add_argument("--depends-on", nargs="+", default=[], metavar="ID")
    p.add_argument(
        "--authorized", action="store_true",
        help="confirms a human authorized touching the dependency graph",
    )
    p.set_defaults(func=cmd_add_card)

    p = sub.add_parser("link", help="adds dependencies to an already existing card")
    p.add_argument("id")
    p.add_argument("--depends-on", nargs="+", required=True, metavar="ID")
    p.add_argument(
        "--authorized", action="store_true",
        help="confirms a human authorized touching the dependency graph",
    )
    p.set_defaults(func=cmd_link)

    p = sub.add_parser("assign", help="moves one or more cards to In progress")
    p.add_argument("ids", nargs="+", metavar="ID")
    p.add_argument("--by", choices=["agent", "human"], default="agent")
    p.add_argument("--worker", help="who works it, e.g. 'parent' or 'sub-cap3' -- tells parent and subagents apart")
    p.add_argument(
        "--takeover", action="store_true",
        help="take a card that's already In progress for someone else (e.g. its subagent died)",
    )
    p.set_defaults(func=cmd_assign)

    p = sub.add_parser("describe", help="updates description and/or sources (frontmatter) of an existing card")
    p.add_argument("id")
    p.add_argument("--text", default=None, help="one-line description")
    p.add_argument(
        "--sources", nargs="+", default=None, metavar="PATH",
        help="paths to external files (txt/md/tex/docx) relevant to this card -- replaces the previous list entirely",
    )
    p.set_defaults(func=cmd_describe)

    p = sub.add_parser("propose", help="uploads a content proposal for review")
    p.add_argument("id")
    p.add_argument("--file", required=True, help="path to the draft with the proposed content")
    p.set_defaults(func=cmd_propose)

    p = sub.add_parser("diff", help="shows the diff between the current content and the pending proposal")
    p.add_argument("id")
    p.add_argument("--no-evals", action="store_true", help="only the diff, without the evals summary on top")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("classify", help="assigns a tier (fast/exec/reasoning) to cards from their context, with the reason")
    p.add_argument("ids", nargs="*", metavar="ID")
    p.add_argument("--all", action="store_true", help="every card that isn't Approved")
    p.add_argument("--dry-run", action="store_true", help="print tiers and a summary, write nothing")
    p.set_defaults(func=cmd_classify)

    p = sub.add_parser("model", help="records the concrete model for cards: --proposed (agent) or --approved (human's yes)")
    p.add_argument("ids", nargs="+", metavar="ID")
    p.add_argument("--proposed", metavar="MODEL", help="the model the agent proposes, e.g. 'Sonnet 5.5'")
    p.add_argument("--approved", metavar="MODEL", help="the model the human approved (may differ from the proposed one)")
    p.add_argument("--authorized", action="store_true", help="the human said yes to --approved in this conversation")
    p.add_argument("--clear", action="store_true", help="remove both models")
    p.set_defaults(func=cmd_model)

    p = sub.add_parser(
        "eval",
        help="runs the vault's evals on a draft without proposing it -- no state change, no attempt spent",
    )
    p.add_argument("id")
    p.add_argument("--file", required=True, help="the draft to check")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("context", help="bundles description+approved dependencies+sources+PROJECT.md+ARCHITECTURE.md to work the card")
    p.add_argument("id")
    p.add_argument(
        "--for-subagent", action="store_true",
        help="self-contained brief for a subagent that starts from zero: adds AGENTS.md and what to do when done",
    )
    p.set_defaults(func=cmd_context)

    p = sub.add_parser("approve", help="applies the pending proposal to the real .md")
    p.add_argument("id")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("reject", help="discards the pending proposal")
    p.add_argument("id")
    p.add_argument("--feedback", default=None)
    p.set_defaults(func=cmd_reject)

    p = sub.add_parser(
        "retry",
        help=f"(human) give a card whose proposals failed the evals {context.MAX_ATTEMPTS} times a fresh set of attempts",
    )
    p.add_argument("id")
    p.set_defaults(func=cmd_retry)

    p = sub.add_parser(
        "accept-edit",
        help="(human) accept your own hand edits to content/<id>.md, so the content_untouched eval stops flagging them",
    )
    p.add_argument("ids", nargs="+", metavar="ID")
    p.set_defaults(func=cmd_accept_edit)

    p = sub.add_parser("status", help="lists cards grouped by status")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser(
        "guide",
        help="changes to PROJECT.md / ARCHITECTURE.md: the agent proposes, the human approves (like a card)",
    )
    guide_sub = p.add_subparsers(dest="guide_action", required=True)
    g = guide_sub.add_parser("propose", help="the agent proposes a new version of the file")
    g.add_argument("name", help="PROJECT.md or ARCHITECTURE.md")
    g.add_argument("--file", required=True, help="draft with the whole new file")
    g = guide_sub.add_parser("diff", help="what the pending change would change")
    g.add_argument("name", help="PROJECT.md or ARCHITECTURE.md")
    g = guide_sub.add_parser("approve", help="(human) write the pending change; the old version is kept in approved/guides/")
    g.add_argument("name", help="PROJECT.md or ARCHITECTURE.md")
    g = guide_sub.add_parser("reject", help="(human) discard the pending change")
    g.add_argument("name", help="PROJECT.md or ARCHITECTURE.md")
    g.add_argument("--feedback", help="why -- the agent sees it in 'histos context'")
    p.set_defaults(func=cmd_guide)

    p = sub.add_parser("validate", help="validates project.canvas against the formal schema")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser(
        "arrange",
        help="re-lays out the whole board, moving every card -- only when the human asks for it",
    )
    p.add_argument(
        "--set-aside-done", action=argparse.BooleanOptionalAction, default=True,
        help="moves approved cards with nothing pending depending on them into a separate 'Done' group (default: on)",
    )
    p.add_argument(
        "--prune-redundant", action=argparse.BooleanOptionalAction, default=True,
        help="removes arrows already implied by a longer path, keeping them in the card's "
             "implied_dependencies so 'context' still sees them (default: on)",
    )
    p.set_defaults(func=cmd_arrange)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Card content is arbitrary UTF-8, but Windows encodes piped output (how agents read it) as cp1252.
    if (sys.stdout.encoding or "").lower().replace("-", "") != "utf8" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
