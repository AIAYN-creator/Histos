# Instructions for agents in this vault

This directory is a vault managed by **Histos**: a task board in `project.canvas` (Obsidian Canvas) where each card's color is its status, and the real content lives in `content/*.md`. The `histos` CLI is the only supported way to touch the canvas -- never edit `project.canvas` by hand.

## Hard rules (non-negotiable)

1. **Never write directly to `content/*.md`, or to any file registered as a `sources` entry on a card.** Both are read-only for you. The only path for a content change to reach the canonical `.md` is `histos propose <id> --file <draft>` followed by `histos approve <id>` from a human. If you need to draft content, write it to a separate file (the draft) and pass it to `propose` -- never edit `content/<id>.md` directly. `propose` copies the draft to `propuestas/<id>.md` (a visible folder in Obsidian, not hidden) until it's approved or rejected; the human may read it or even tweak it there before deciding -- that's their call, this rule is only for you. On approval, that copy is archived to `aprobados/<id>.md` (history); on rejection it's discarded without a trace. `sources` (see `describe --sources`) are the human's reference material -- a Word doc with a bibliography, a `.tex` in Overleaf -- never edit or "fix" them, not even something that looks like an obvious error: if a change is needed there, say so in the conversation, don't touch it yourself. If you're Claude Code: `content/**` is also denied in `.claude/settings.json` -- if an `Edit`/`Write`/`Bash` gets rejected for permissions there, that's intentional, don't try to work around it.
2. **Never pass `--authorized` without a human having given you explicit permission in the current conversation.** Applies to `add-card --depends-on` and to `link` (for adding a dependency to an already existing card). Ask for permission first (say which dependency you want to create and why), wait for the answer, and only then pass `--authorized`.
3. **No permission needed** for: creating standalone cards (no `--depends-on`), assigning cards (`assign`), updating description/sources (`describe`), proposing content (`propose`), or checking status (`status`, `diff`, `context`, `validate`).

## States (card color)

| Color | Preset | Status | What it means |
|---|---|---|---|
| purple | `"6"` | Backlog | ready to start |
| orange | `"2"` | In progress | assigned, being worked on |
| red | `"1"` | Blocked | derived from the graph -- don't assign by hand, it recalculates itself |
| yellow | `"3"` | Proposal pending review | waiting for a human's `approve`/`reject` |
| cyan | `"5"` | Dependency change request | pending authorization (rule 2) |
| green | `"4"` | Approved | done |

## Starting a new project (vault with no cards yet)

If `histos status` shows no cards, don't jump straight into creating the index on your own. Before the first `add-card`, ask the human (it's a normal conversation, no CLI needed for this):

1. **What the project is about** -- one sentence is enough, but without it cards come out generic.
2. **What type of work it is**: experimental/research, literature review, or mixed -- this changes quite a bit of what goes in each card (especially if there's a "Results"/"Methodology"-type section).
3. **Whether there's a mandatory structure** (university, journal, or organization template) to follow to the letter, or whether we start from a standard structure for that type of project and adjust it together.
4. **Whether it makes sense to add checkpoints** (periodic reviews with an advisor/editor/lead) as their own cards, not just content chapters.

With the answers, propose a table of cards + dependencies (id, title, what it depends on) and ask for explicit confirmation before creating anything with dependencies -- standalone cards don't need authorization (rule 3), but as soon as the table has a dependency, they do (rule 2). Once confirmed, create the cards in topological order.

## Commands

Always start with `histos status` to see what's there. Then:

```
histos add-card <id> --title "..." [--description "..."] [--depends-on ID...] [--authorized]
histos link <id> --depends-on ID [ID...] --authorized   # adds a dependency to an EXISTING card
histos describe <id> [--text "..."] [--sources PATH...]  # frontmatter only, no authorization needed
histos assign <id> [id...] [--by agent|human]
histos context <id>                                      # description+approved dependencies+sources+PROJECT.md
histos propose <id> --file <draft.md>
histos diff <id>
histos approve <id>
histos reject <id> [--feedback "..."]
histos validate
```

`histos <command> --help` for the details of each flag. Before proposing content for a card with dependencies, run `histos context <id>` instead of reading each `content/<dep>.md` by hand -- it bundles everything for you (including external files the human registered with `describe --sources`, e.g. a Word doc with a bibliography).

## Unsupervised mode (AFK)

If a human assigns you several cards and leaves, you can keep working the queue without asking for permission at each step: no Histos command blocks on a prompt. The worst case possible while following the rules above is leaving cards yellow, waiting for review -- never content written without permission, or dependencies changed without authorization.
