# Usage guide

For the full design see the [README](../README.md); this is a practical "how this actually gets used day to day" guide. For how to organize the work itself — card size, rhythm, when to use subagents — see [workflow.md](workflow.md).

## Install

See [install.md](install.md) — covers the terminal tool, the desktop app, or both.

## Terminal or desktop app — your choice

Histos has two front ends for the parts you do yourself, and they're genuinely interchangeable: both call the exact same code underneath (`operations.py`), so approving a card from the app and running `histos approve` do the identical thing to your project. Pick whichever you're more comfortable with, switch anytime, even mix them.

| What | Terminal | Desktop app |
|---|---|---|
| Set up a new project | `histos init` | Choose the folder → **Start project here** |
| See where things stand | `histos status` | The overview screen |
| Review a proposal | `histos diff <id>` | Click the card under "Proposal pending review" |
| Accept it | `histos approve <id>` | **Approve** |
| Send it back | `histos reject <id> --feedback "..."` | **Reject** (same optional feedback) |
| Open the board in Obsidian | (open Obsidian yourself) | **Open in Obsidian**, or **Show folder** if that doesn't work — see the README's Trust model for why it might not |
| Tidy up the whole board | `histos arrange` | — |
| Bring an older vault to v3 | `histos init --add-config` | — |
| Give a card more attempts after failed evals | `histos retry <id>` | — |
| Accept your own hand edits to a card | `histos accept-edit <id>` | — |
| Accept or refuse a change to `PROJECT.md`/`ARCHITECTURE.md` | `histos guide approve/reject <file>` | — |

The desktop app covers the first six rows; the rest are yours too, but terminal-only. Everything else (`add-card`, `link`, `describe`, `assign`, `classify`, `model`, `context`, `eval`, `propose`, `guide propose`, `validate`) is what the agent runs on your behalf, so it only shows up below as terminal commands. The tier and model of each card, and the evals, are visible in the terminal (`status`, `diff`); the desktop app doesn't show them yet.

## Starting a project

1. **Create a new folder** for the project, separate from any existing vault — e.g. `C:\Users\you\Projects\MyProject`.

2. **Set up the vault**, *before* opening the agent — terminal or desktop app, same result:

   ```bash
   cd /path/to/your/project
   histos init
   ```

   Or in the desktop app: open it, choose this folder, click **Start project here**.

   Either way, this creates `project.canvas` (with the color legend already in place), `content/`, the agent instructions (`AGENTS.md`, `CLAUDE.md`), `histos.config.yaml` and an `evals/` folder. Order matters: until these files exist, a new agent has no way of knowing this is a Histos project.

3. **Optionally, write `PROJECT.md` and `ARCHITECTURE.md`** in the same folder: what the project is, and how it's written (style, citation format, structure — or, for code, the stack and conventions). The agent reads both before every card. After this, it can only *propose* changes to them (see [below](#projectmd-and-architecturemd)).

4. **Open that same folder as a vault in Obsidian** — Canvas is a native feature, no plugin needed — and you'll see the board with the legend at the top. **Important:** the folder you open has to be exactly the one containing `project.canvas`, never one above it — otherwise Obsidian can't find the cards' `.md` files and you'll see them as "Create new note" instead of with content.

5. **Open a new agent session** (a different window/conversation) with that folder as the working directory. Claude Code loads `CLAUDE.md` automatically on startup, which in turn imports all of `AGENTS.md` — you don't need to explain anything about Histos to it.

6. **Tell it what the project is about**, nothing more — something natural like "I want to organize my thesis on X" or "help me set up this writing project." If `AGENTS.md` is doing its job, the agent asks what it's about, what type of work it is, whether there's a mandatory template, and whether review checkpoints make sense — and from that it proposes a card table with dependencies, which you can adjust before confirming.

7. **Refresh Obsidian (`Ctrl+R`) after the agent creates the cards.** The canvas gets edited from outside Obsidian, and Obsidian doesn't always notice on its own that `project.canvas` changed while you had it open.

## Reading the board

Each card's color is its status — the same colors and meaning whether you're looking at the canvas in Obsidian or at the desktop app's overview screen:

| Color | Status |
|---|---|
| purple | Backlog — ready to start |
| orange | In progress |
| red | Blocked (recalculates itself, don't touch it by hand) |
| yellow | Proposal pending review — **your turn to review** |
| cyan | Dependency change request — reserved: no command sets it yet |
| green | Approved |

When you see a yellow card, it's your turn. When the agent needs a new dependency between cards, it doesn't color anything: it asks you in the conversation, and only adds it once you say yes.

`histos status` lists the same thing in the terminal, plus each card's tier and model and who's working it:

```
In progress (1)
  - intro [sub-intro]  (content/intro.md)  {fast · Haiku 5.5}  -- Motivate the problem and state the contributions
Proposal pending review (1)
  - related  (content/related.md)  {reasoning · Opus 5.5}  -- Compare the three main approaches and their trade-offs
...
Tiers: fast 2 · exec 0 · reasoning 1 · unclassified 2
Waiting for your model approval: method (fast, proposed Sonnet 5.5)
```

`[sub-intro]` is the card's worker (here, a subagent); `{fast · Haiku 5.5}` its tier and approved model; `proposed Sonnet 5.5?` a model the agent proposed that you haven't approved yet.

## The day-to-day cycle

1. **You say which card(s) to work on.** The agent assigns them (`histos assign`).
2. **It classifies each one** (`histos classify`): a tier — `fast`, `exec` or `reasoning` — with the reason, from the card's description, dependencies and sources.
3. **It proposes a model with that reason**, e.g. *"I'll do cap3 with Sonnet 5.5 (tier exec: it has code blocks)"*. You say yes, or pick another; it records your answer.
4. **It does the card itself or hands it to a subagent** (see [Subagents](#subagents)).
5. **It proposes the result** (`histos propose`). The evals run first: if the draft fails one — a leftover `TODO`, too short — the card stays orange and the agent fixes it and tries again, three times at most. Only a draft that passes turns the card **yellow**. **It hasn't touched the real `.md` yet.**
6. **You review it** — `histos diff <id>` shows which evals it passed and in which attempt, then the text diff; or open `proposals/<id>.md` in Obsidian like any other note (you can even tweak it before approving). In the desktop app, click the card for a plain before/after comparison.
7. **You decide.** `histos approve <id>` (or **Approve**) writes the real `.md` and archives the approved draft in `approved/<id>.md`. `histos reject <id> --feedback "what it's missing"` (or **Reject**) discards it, and the agent gets your feedback the next time it runs `histos context` on that card.

(Vaults created before Histos's English rename use `propuestas/`/`aprobados/` instead of `proposals/`/`approved/` — same folders, same behavior, just the old names. `histos` detects whichever one your vault already has and keeps using it.)

## Tiers and models

A **tier** is how demanding a card looks: `fast` (short, simple, little context), `exec` (the normal case, and the default), `reasoning` (decisions, design, debugging, synthesis, or a lot of context). `histos classify` explains every rule it checked:

```
method -> fast
  reasoning (any): no match -- no architecture terms; no debugging terms; no multi-step wording; token_count = 81
  exec (any): no match -- no code blocks; no file operations terms
  fast (all): match -- token_count = 81; dependency_count = 1; no code blocks; no file operations terms
```

Histos never picks the model: the agent translates the tier to a model of its platform and proposes it, and **you approve it**, like a dependency. For Claude Code, `AGENTS.md` suggests fast → Haiku 5.5, exec → Sonnet 5.5, reasoning → Opus 5.5 — change that table in `AGENTS.md` if you prefer others.

The tier is a suggestion with its reason, not a verdict — with one-line descriptions the heuristics have little to go on, so read the reason and overrule it freely. Richer descriptions give better tiers. The rules live in `histos.config.yaml` ([format](canvas-schema.md#histosconfigyaml)); only you edit it.

## Evals

Evals are a filter that runs before a draft reaches you — they catch obvious junk, you still review everything that passes. A new vault comes with two active ones, in `evals/basics.yaml`:

- **`no_direct_writes`** — nothing changed `content/` behind Histos's back. It also fires if *you* edit a card's text by hand in Obsidian: that's fine, run `histos accept-edit <id>` and it accepts your version.
- **`no_placeholders`** — no leftover `TODO`, `TBD`, `lorem ipsum`, `[...]`.

`evals/examples.yaml` has two more, switched off because they depend on the project: a word minimum and required headings. Uncomment and adjust them when you want them; `only: ["cap*"]` / `except: ["cod-*"]` aim an eval at some cards only. The format and every check: [canvas-schema.md](canvas-schema.md#evals).

When a card has failed its evals three times in a row, the agent stops trying and leaves it orange, with the reasons in its frontmatter (`eval_results`). Look at them: either the draft needs your input, or an eval is too strict. `histos retry <id>` gives it three more attempts.

The agent can't edit `evals/` or `histos.config.yaml` — it would be grading its own homework.

## Subagents

A **subagent** is a second agent session the main one (the "parent") starts for a single card, with a clean context: it gets only `histos context <id> --for-subagent` — the rules, the card, its approved dependencies, `PROJECT.md`/`ARCHITECTURE.md`, and the approved model — and it proposes its draft itself. The parent only gets back a short summary, so its own context stays free for coordinating with you.

The agent uses one when a card is self-contained (nothing to ask you while it's worked), has no open decision, and is worth it (large, or able to run while the parent does another card) — at most two at a time, each on its own card. Decision cards stay with the parent, worked with you. In `status`, `[parent]` or `[sub-cap3]` after a card says who has it.

If a subagent dies halfway, its card stays orange under its name; `histos assign <id> --worker parent --takeover` hands it back — the agent only does that when you tell it to.

## PROJECT.md and ARCHITECTURE.md

The agent never edits them directly. When it thinks one should change, it proposes the whole new version with `histos guide propose`, and tells you. `histos status` shows "Guides pending review"; then:

```bash
histos guide diff PROJECT.md       # what would change
histos guide approve PROJECT.md    # write it; the previous version goes to approved/guides/
histos guide reject PROJECT.md --feedback "keep the scope as it is"
```

## Keeping the board tidy

**Nothing on the board moves on its own.** Approving a card only changes its color (and turns the cards that were waiting on it from red to purple). A new card lands one column to the right of its dependencies, at their height, without pushing anything else. Cards you dragged somewhere by hand stay exactly there. So as weeks go by, the board slowly drifts out of shape.

When that happens — or simply after a batch of approvals — tidy it up from the vault folder:

```bash
histos arrange
```

Then reload the canvas in Obsidian (`Ctrl+R`). What it does:

- **Columns by dependency**, left to right, with related cards next to each other so arrows cross as little as possible.
- **No arrow across a card:** wherever an arrow skips over a column, the cards in that column make room for it.
- **"Not connected" group:** cards with no arrows at all, in or out — often ideas for later, or a dependency nobody linked yet (worth a look).
- **"Done" group:** approved cards where everything that depends on them is approved too — finished history, out of the way of the active work.
- **Redundant arrows removed:** if a card depends on A both directly and through B, the direct arrow to A goes. Nothing is lost: the card keeps A in its `implied_dependencies`, and `histos context` still hands A to the agent.

Two switches, both on by default: `--no-set-aside-done` keeps finished cards among the rest, `--no-prune-redundant` keeps every arrow.

It's the only command that moves cards that already exist, including the ones you placed by hand — that's why it only ever runs when you ask. The previous layout is saved as `project.canvas.bak`: to undo, close the canvas in Obsidian and put the `.bak` back in place of `project.canvas`. It only keeps the layout from just before the *last* `arrange`.

## Why it's safe to leave it working unsupervised

The agent can **never** write to `content/*.md` without going through `propose`/`approve`, can **never** touch the dependency graph or settle a model without your yes in the conversation (rules in `AGENTS.md`, backed by the `--authorized` flag), and its drafts go through the evals before they reach you.

Before you leave, let it get the queue ready: classify the cards, propose their models, and approve them all at once. `histos status` should show nothing under "Waiting for your model approval". While you're away it only works cards with an approved model. What you find when you get back:

- yellow cards that passed the evals, waiting for you;
- orange cards that used up their three attempts, with the reasons in `eval_results`;
- untouched cards that had no approved model;

— never content written without your permission, never a model you didn't approve. Several agents (a parent and its subagents) can work at once: every command that changes the vault holds a lock, so nobody overwrites anybody.

How much of this is enforced and how much is convention: see the README's [Trust model](../README.md#trust-model).

## Upgrading a vault from v2

```bash
histos init --add-config
```

adds `histos.config.yaml` and `evals/` and touches nothing else. `init` never overwrites an existing `AGENTS.md`, so copy the new one over the old one by hand — from [`src/histos/templates/AGENTS.md`](../src/histos/templates/AGENTS.md) — or the agent won't know about tiers, models or evals. Cards created before v3 have no `content_hash`; `content_untouched` compares them with their approved copy instead, so nothing breaks.

## Other useful commands

These are what the agent runs for itself — terminal/agent-only:

- `histos describe <id> [--text "..."] [--sources path1 path2 ...]` — sets or changes a card's description and/or its list of external reference files (`.txt`, `.md`, `.tex`, `.docx` — e.g. the Word doc with your bibliography). Paths can be anywhere on disk. `--sources` replaces the whole list. It doesn't touch content, so it doesn't need approval.
- `histos context <id>` — bundles into one block of text: the card's description, tier and model, sources and your rejection feedback; the same for each dependency (plus its approved content), including the ones `arrange` stopped drawing; and `PROJECT.md`/`ARCHITECTURE.md`. `--for-subagent` wraps it in a brief for a subagent.
- `histos eval <id> --file <draft>` — runs the evals on a draft without proposing it, so the agent can check its work without spending an attempt.
- `histos link <id> --depends-on ID [ID...] --authorized` — for a dependency discovered after the card was created.

**Sources aren't picked up by location.** Having a Word doc or a `.tex` open in the vault folder doesn't make it get used: you register the path once with `describe --sources`, and `context` uses it from then on. That's deliberate — without the registration there's no reliable way to know which loose file belongs to which card.

## If something looks off

```bash
histos validate
```

Checks `project.canvas` against the formal schema (broken references, dependency cycles, malformed cards), every card's frontmatter (and that its `.md` exists), `histos.config.yaml` and the `evals/` folder, and tells you exactly what's wrong. Terminal-only.

## Full reference

Every command with its flags is in the [README's CLI section](../README.md#cli), or `histos <command> --help`. The vault format — canvas, frontmatter, config, evals — is in [canvas-schema.md](canvas-schema.md).
