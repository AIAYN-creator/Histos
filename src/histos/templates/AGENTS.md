# Instructions for agents in this vault

A **Histos** vault: `project.canvas` (Obsidian Canvas) is the board, each card's color is its status, content lives in `content/*.md`. Touch the canvas only through the `histos` CLI. **You propose, the human approves**: content, dependencies, models, guidance files.

## Hard rules

1. **Never write to `content/*.md` or to a card's `sources`.** Write a draft elsewhere and `histos propose <id> --file <draft>`; the human approves. If a source needs a change, say so. Direct writes are caught by the `content_untouched` eval.
2. **`PROJECT.md` and `ARCHITECTURE.md` are the human's:** change them only with `histos guide propose <file> --file <draft>`.
3. **Never touch `histos.config.yaml` or `evals/`.** If an eval looks wrong, say so. (Desktop notifications can be muted by the human with `histos notify [--on | --off | --status]`, `HISTOS_NO_NOTIFY=1`, or `notifications.enabled: false` in `histos.config.yaml`).
4. **`--authorized` only after the human's explicit yes in this conversation** (dependencies via `add-card --depends-on`/`link`, and `model --approved`). Ask first, saying what and why. A plan's dependencies need no flag: `plan approve` is the yes.
5. **Never run the human's commands:** `approve`, `reject`, `retry`, `accept-edit`, `guide approve`, `guide reject`, `plan approve`, `plan reject`, `pause`, `notify`, `undo`, `init --add-config`.
6. **`histos arrange` only when the human asks.** Never change arrow colors: magenta is Histos's critical path, the rest are the human's.
7. **Never take a card In progress for someone else.** `--takeover` only when the human says so.

Free to use: `add-card` (no `--depends-on`), `assign`, `describe`, `classify`, `model --proposed`, `eval`, `propose`, `guide propose`, `plan propose`, and the reading commands. In Claude Code, denied edits in `.claude/settings.json` are intentional: don't work around them.

Colors: purple Backlog · orange In progress · red Blocked (derived, never set it) · yellow Proposal pending review · cyan reserved · green Approved.

## New project: the interview

If `histos status` shows no cards, create none. Be a consultant, with your strongest model:

1. Listen to the whole idea.
2. Ask in rounds of at most 5 questions, most consequential first: purpose, deliverable, what it is *not*, when it's done, type of work, templates, checkpoints. Ask about gaps; never fill them with guesses.
3. Push back: what you'd change (approach, stack, scope, risks), each with a reason and a concrete alternative. With no objections, say why the plan holds.
4. Write `PROJECT.md` and `guide propose` it (`histos eval PROJECT.md --file <draft>` is the dry run). Sections: goal, deliverable, audience, out of scope, done when, constraints, what I understood (your words), what I'd change and why, decisions. Carry over pending decisions from `histos context`. For code, also `ARCHITECTURE.md`: stack, conventions, how to run the tests.
5. Only after `PROJECT.md` is approved, propose the plan.

No `histos.config.yaml`? Suggest the human run `histos init --add-config`.

## Planning

The whole card table at once, with `histos plan propose --file <plan.yaml>`, then walk the human through `histos plan diff`:

```yaml
summary: How you cut the work and why.
cards:
  - id: dec-format
    title: Decide the format
    done_when: Approved, and cod-format can implement it without questions.
  - id: cod-format
    title: Implement the format
    done_when: Every command writes it, with tests.
    depends_on: [dec-format]
```

Every `done_when` must be checkable. No tiers in the plan. After a rejection, `plan diff` shows why: answer it. Single cards later: `add-card --done-when`.

## Working a card

1. `histos status`. **Never estimate time yourself**: status shows estimates from the human's history; "no data" is the answer.
2. `histos assign <id> --worker <you>` (`parent`, or `sub-<id>` for a subagent).
3. `histos classify <id>` right before working: tier `fast`, `exec` or `reasoning`.
4. Propose the model with its reason and `histos model <id> --proposed "<model>"`. Claude Code: fast → Haiku 5.5 · exec → Sonnet 5.5 · reasoning → Opus 5.5.
5. After the human's yes: `histos model <id> --approved "<model>" --authorized`, even on an already approved card.
6. `histos context <id>`: card, dependencies (implied ones too), sources, guidance files, and feedback from a rejection (address it).
7. Optional dry run: `histos eval <id> --file <draft>`.
8. `histos propose <id> --file <draft>`. Evals run first; on failure, fix and propose again. **3 attempts at most**, then leave the card for the human.
9. If the approved card still has to be built (code), `histos implement <id>` before you start and `histos implement <id> --done` when it's finished: that time is yours too.
   - **Commits (code):** commit only with the human's explicit permission. Keep the human as sole author (never `Co-Authored-By`) and add the trailers `Supervised-by: <your model>` and `Coordinated-by: Histos (<card-id>)`. (To turn trailers off, remove this line from `AGENTS.md`.)

**Subagents** only for a card that is self-contained (`histos context <id> --for-subagent` is enough), has no open decision, and pays off. `dec-` cards are always yours. Assign it `--worker sub-<id>`, give that brief as the whole prompt, with the approved model. At most 2 at a time.

**Before the human leaves (AFK):** classify the queue, propose models, get them approved in one go (`histos model id1 id2 ... --approved "<model>" --authorized`), and remind them to run `histos pause`. While away, work only cards with an approved model; a reclassified card loses its model, so leave it.

## Commands

```
histos status | validate | doctor | report | diff <id> | plan diff
histos plan propose --file <plan.yaml>
histos add-card <id> --title "..." [--description "..."] [--done-when "..."] [--depends-on ID... --authorized]
histos link <id> --depends-on ID... --authorized
histos describe <id> [--text "..."] [--sources PATH...]
histos assign <id>... [--worker NAME]
histos classify <id>... | --all
histos model <id>... --proposed "..." | --approved "..." --authorized
histos context <id> [--for-subagent]
histos eval <id>|PROJECT.md --file <draft>
histos propose <id> --file <draft>
histos implement <id> [--done]
histos guide propose PROJECT.md|ARCHITECTURE.md --file <draft>
```

`histos <command> --help` for details.

<!-- histos-template-version: 3.4.0 -->
