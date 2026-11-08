# Instructions for agents in this vault

This directory is a vault managed by **Histos**: a task board in `project.canvas` (Obsidian Canvas) where each card's color is its status, and the real content lives in `content/*.md`. The `histos` CLI is the only supported way to touch the canvas -- never edit `project.canvas` by hand.

The core rule behind everything below: **you propose, the human approves.** That covers content, dependencies, the model each card is worked with, and changes to the project's guidance files.

## Hard rules (non-negotiable)

1. **Never write directly to `content/*.md`, or to any file registered as a `sources` entry on a card.** Both are read-only for you. The only path for a content change is `histos propose <id> --file <draft>` followed by the human's `histos approve <id>`. Write your draft to a separate file and pass it to `propose`. `proposals/<id>.md` is a visible folder; the human may read or tweak it there. `sources` (see `describe --sources`) are the human's reference material -- a Word doc with a bibliography, a `.tex` in Overleaf -- never edit or "fix" them: if a change is needed there, say so in the conversation. A direct write is caught anyway: the `content_untouched` eval fails every proposal until the human sorts it out.
2. **`PROJECT.md` and `ARCHITECTURE.md` are the human's too.** To change them, `histos guide propose <file> --file <draft>`, tell the human in the conversation, and let them `histos guide approve` or `reject` it. Feedback from a rejected change shows up in `histos context`.
3. **Never touch `histos.config.yaml` or anything in `evals/`.** They are the human's filter on your work; you never relax them, not even to get a proposal through. If an eval looks wrong, say so.
4. **Never pass `--authorized` without the human's explicit yes in the current conversation.** It applies to dependencies (`add-card --depends-on`, `link`) and to the model a card is worked with (`model --approved`). Ask first (say what and why), wait for the answer, then pass it. (The dependencies in a plan need no flag: the human's `plan approve` is the yes.)
5. **Never run the human's commands:** `approve`, `reject`, `retry`, `accept-edit`, `guide approve`, `guide reject`, `plan approve`, `plan reject`, `pause`, `init --add-config`. They are how the human keeps the last word.
6. **Never run `histos arrange` unless the human asks for it in the current conversation.** It moves every card, including the ones they placed by hand. Arrow colors aren't yours either: the magenta critical path is Histos's, every other color the human's.
7. **Never take a card that is In progress for someone else.** `assign` refuses it; `--takeover` exists for a card whose worker is gone (e.g. a subagent that died), and you only use it when the human says so.

**No permission needed** for: standalone cards (`add-card` without `--depends-on`), `assign`, `describe`, `classify`, `model --proposed`, `eval`, `propose`, `guide propose`, `plan propose`, and reading (`status`, `diff`, `plan diff`, `context`, `validate`).

If you're Claude Code: `content/**`, `project.canvas`, `PROJECT.md`, `ARCHITECTURE.md`, `histos.config.yaml` and `evals/**` are also denied in `.claude/settings.json` -- a rejected `Edit`/`Write`/`Bash` there is intentional, don't work around it.

## States (card color)

| Color | Preset | Status | What it means |
|---|---|---|---|
| purple | `"6"` | Backlog | ready to start |
| orange | `"2"` | In progress | assigned, being worked on |
| red | `"1"` | Blocked | derived from the graph -- don't assign by hand, it recalculates itself |
| yellow | `"3"` | Proposal pending review | passed the evals, waiting for the human's `approve`/`reject` |
| cyan | `"5"` | Dependency change request | reserved -- no command sets it; to change a dependency, ask (rule 4) |
| green | `"4"` | Approved | done |

## Starting a new project: the interview

If `histos status` shows no cards, don't create any. Projects go back and forth when the idea never got across at the start: be a consultant, not a stenographer, with your strongest model (tier `reasoning`).

1. **Listen** to the whole idea before proposing anything.
2. **Ask in rounds** of at most 5 questions, most consequential first: what it's for, what gets delivered, what it is *not*, when it's done; then type of work, mandatory templates, review checkpoints. **Ask about a gap, never fill it** with a guess.
3. **Push back.** Say what you'd change -- approach, pipeline, language or stack, scope, risks -- each with its reason and a concrete alternative, even when the human sounds sure; with no objections, say why the plan holds up. The human decides on each.
   - Weak: *"Another language could also work."*
   - Useful: *"In Python the parser needs a C extension to be fast enough; Rust avoids that, at the cost of a slower first week."*
4. **Write `PROJECT.md`** and `histos guide propose` it (`histos eval PROJECT.md --file <draft>` is the dry run). Sections: goal, deliverable, audience, out of scope, done when, constraints, **what I understood** (in *your* words), **what I'd change and why**, decisions -- Spanish or English headings. Carry any "Decisions not yet in PROJECT.md" from `histos context` into it. For code, also `ARCHITECTURE.md`: stack, conventions, how to run the tests.
5. **Only once `PROJECT.md` is approved, propose the plan.**

If the vault has no `histos.config.yaml` (created before v3), suggest the human run `histos init --add-config`.

## Planning cards

Propose the whole card table at once, as one plan the human approves or rejects -- no card exists until then:

```yaml
summary: How you cut the work and why -- the first thing the human reads.
cards:
  - id: dec-format
    title: Decide the format
    done_when: The format is approved and cod-format can implement it without questions.
  - id: cod-format
    title: Implement the format
    description: optional, one line
    done_when: Every command writes it, with tests.
    depends_on: [dec-format]          # cards in this plan, or already on the board
```

`histos plan propose --file <plan.yaml>`, then walk the human through `histos plan diff`. Every `done_when` must be checkable -- "it works" isn't. No tier in the plan. After a `plan reject`, `plan diff` shows why: answer it in the next plan. Single cards later: `add-card --done-when`.

## Working a card

1. `histos status` -- see what's there. **Never estimate time yourself:** status has each card's estimate from the human's own history, and the critical path; if it says "no data", that's the answer.
2. `histos assign <id> --worker <you>` -- e.g. `--worker parent`, or `sub-<id>` for a card you'll hand to a subagent.
3. `histos classify <id>` -- the tier (`fast`, `exec` or `reasoning`) and why. Classify right before working, never when the queue is created: the tier depends on the approved content of the card's dependencies.
4. **Propose the model.** Translate the tier to a model of your platform and say it with the reason: *"I'll do cap3 with Sonnet 5.5 (tier exec: it has code blocks)"*. Record it with `histos model <id> --proposed "<model>"`. In Claude Code, suggest: fast → Haiku 5.5 · exec → Sonnet 5.5 · reasoning → Opus 5.5.
5. **Wait for the human's yes** (or their different choice), then `histos model <id> --approved "<model>" --authorized` -- even if they approved the card in the same message: it still records on an approved card with no model yet.
6. **Do it yourself or hand it to a subagent** (next section).
7. `histos context <id>` gathers everything for the draft: the card, its dependencies (including "implied" ones whose arrow `arrange` removed -- treat them like any other), sources, `PROJECT.md`, `ARCHITECTURE.md`, and the feedback from a rejected proposal ("Feedback on a rejected proposal": address it).
8. Optionally `histos eval <id> --file <draft>` -- checks the draft against the evals without spending an attempt.
9. `histos propose <id> --file <draft>`. The evals run first. If they fail, the card stays In progress and the output says what to fix: fix it and propose again. **3 attempts at most**; after the third failure `propose` refuses, and you leave the card for the human and move on.

## Subagents

Hand a card to a subagent only when all three hold:

- it's **self-contained**: `histos context <id> --for-subagent` is all it needs, no questions for the human;
- there's **no open decision** the human has to make while it's being worked;
- it **pays off**: it's big, or it can run while you work on another card.

Otherwise do it yourself. `dec-` (decision) cards are always yours, worked with the human.

To launch one: assign the card to it (`--worker sub-<id>`), get its brief with `histos context <id> --for-subagent`, and start the subagent with that brief as its whole prompt and the card's approved model. It proposes by itself and returns only a short summary to you. **At most 2 subagents at a time**, each on its own card. Histos doesn't launch anything: how you start a subagent depends on your platform.

## Unsupervised mode (AFK)

Before the human leaves, get the queue ready: `classify` every card in it, propose their models, and let the human approve them in one go (`histos model id1 id2 ... --approved "<model>" --authorized`, once per model). `histos status` lists any card still "Waiting for your model approval". Then remind them to run `histos pause` as they leave: their review clock stops until their next decision, so a night away isn't counted as hours of reviewing.

While they're away, **only work cards with an approved model.** If a reclassification changes a card's tier, its model is cleared: leave that card for the human. No Histos command blocks on a prompt, so you can work the queue; the worst case is cards left yellow (passed the evals, waiting for review), orange (out of attempts, reason in `eval_results`) or untouched (no approved model) -- never content written without permission.

## Commands

```
histos status
histos plan propose --file <plan.yaml>                 # the whole card table, for the human to approve
histos plan diff
histos add-card <id> --title "..." [--description "..."] [--done-when "..."] [--depends-on ID...] [--authorized]
histos link <id> --depends-on ID [ID...] --authorized   # dependency on an EXISTING card (rule 4)
histos describe <id> [--text "..."] [--sources PATH...]
histos assign <id> [id...] [--worker NAME] [--by agent|human]
histos classify <id> [id...] | --all [--dry-run]       # tier and reason
histos model <id> [id...] --proposed "..."              # your proposal
histos model <id> [id...] --approved "..." --authorized # the human's yes (rule 4)
histos context <id> [--for-subagent]
histos eval <id>|PROJECT.md --file <draft.md>           # dry run of the evals, no attempt spent
histos propose <id> --file <draft.md>                   # evals first; 3 attempts at most
histos guide propose PROJECT.md|ARCHITECTURE.md --file <draft.md>
histos diff <id>
histos validate
histos arrange                                          # ONLY when the human asks (rule 6)
```

`histos <command> --help` for the details of each flag.
