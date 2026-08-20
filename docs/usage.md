# Usage guide

For the full design see the [README](../README.md); this is a practical "how this actually gets used day to day" guide.

## Install

See [install.md](install.md) — covers the terminal tool, the desktop app, or both.

## Terminal or desktop app — your choice

Histos has two front ends for the parts you do yourself, and they're genuinely interchangeable: both call the exact same code underneath (`operations.py`), so approving a card from the app and running `histos approve` do the identical thing to your project. Pick whichever you're more comfortable with, switch anytime, even mix them — start a project in the app one day, approve from the terminal the next, whatever fits the moment.

| What | Terminal | Desktop app |
|---|---|---|
| Set up a new project | `histos init` | Choose the folder → **Start project here** |
| See where things stand | `histos status` | The overview screen |
| Review a proposal | `histos diff <id>` | Click the card under "Proposal pending review" |
| Accept it | `histos approve <id>` | **Approve** |
| Send it back | `histos reject <id> --feedback "..."` | **Reject** (same optional feedback) |
| Open the board in Obsidian | (open Obsidian yourself) | **Open in Obsidian**, or **Show folder** if that doesn't work — see the README's Trust model for why it might not |

The desktop app only covers these 5 commands — the ones a human does. The rest (`add-card`, `link`, `describe`, `assign`, `propose`, `context`, `validate`) are what the agent runs on your behalf; there's no reason for a human to run those by hand day to day, so they stay terminal/agent-only and only show up below as terminal commands.

## Starting a project

Actually tested (2026-08-07): a completely fresh agent session, with zero prior context about the project or about Histos, followed these steps without a single issue.

1. **Create a new folder** for the project, separate from any existing vault — e.g. `C:\Users\Usuario\Projects\MyProject`.

2. **Set up the vault**, *before* opening the agent — terminal or desktop app, same result:

   ```bash
   cd /path/to/your/writing-project
   histos init
   ```

   Or in the desktop app: open it, choose this folder, click **Start project here** (it'll offer this automatically once it notices the folder isn't set up yet).

   Either way, this creates `project.canvas` (with the color legend already in place), `content/`, and the agent instructions (`AGENTS.md`, `CLAUDE.md`). Order matters: until these files exist, a new agent has no way of knowing this is a Histos project.

3. **Open that same folder as a vault in Obsidian** — Canvas is a native feature, no plugin needed — and you'll see the board with the legend at the top. **Important:** the folder you open has to be exactly the one containing `project.canvas`, never one above it — otherwise Obsidian can't find the cards' `.md` files and you'll see them as "Create new note" instead of with content.

4. **Open a new agent session** (a different window/conversation) with that folder as the working directory. Claude Code loads `CLAUDE.md` automatically on startup, which in turn imports all of `AGENTS.md` — you don't need to explain anything about Histos to it.

5. **Tell it what the project is about**, nothing more — something natural like "I want to organize my thesis on X" or "help me set up this writing project." If `AGENTS.md` is doing its job, the agent asks what it's about, whether it's experimental/literature-review/mixed, whether there's a mandatory template, and whether review checkpoints make sense — and from that it proposes a card table with dependencies, which you can adjust before confirming. If it jumps straight into creating generic cards without asking anything, ask it explicitly (and flag it, because it means `AGENTS.md` needs a review).

6. **Refresh Obsidian (`Ctrl+R`) after the agent creates the cards.** Just like with content files, the canvas gets edited from outside Obsidian (via the CLI or the desktop app), and Obsidian doesn't always notice on its own that `project.canvas` changed while you had it open.

## Reading the board

Each card's color is its status — the same colors and meaning whether you're looking at the canvas in Obsidian or at the desktop app's overview screen (each status there gets a matching colored dot):

| Color | Status |
|---|---|
| purple | Backlog — pending |
| orange | In progress |
| red | Blocked (recalculates itself, don't touch it by hand) |
| yellow | Proposal pending — **your turn to review** |
| cyan | The agent is asking for authorization to touch a dependency — **your turn to decide** |
| green | Approved |

When you see a yellow or cyan card, it's your turn.

## The day-to-day cycle

1. You ask the agent (Claude Code, Codex, whatever you use) to work one or more cards. The agent runs `histos assign` and starts drafting.
2. The agent uploads its proposal with `histos propose` — the card turns yellow. **It hasn't touched the real `.md` yet.**
3. When you have a moment, review it — `histos diff <id>` shows a text diff, or just open `proposals/<id>.md` in Obsidian like any other note (it's a visible folder, not hidden: you can see it, read it, even tweak it by hand before approving). In the desktop app, the same card shows up under "Proposal pending review" on the overview — click it for a plain-language before/after comparison instead of diff syntax.
4. If you're convinced: `histos approve <id>` (or the **Approve** button) — now the real `.md` gets written, and a copy of what was approved is archived in `approved/<id>.md` (in case you want to compare later, or regret a quick approval). If not: `histos reject <id> --feedback "what it's missing"` (or the **Reject** button, which asks for that same optional feedback) — it's discarded without a trace (rejected drafts aren't archived, no need) and the agent will see your feedback next time it looks at that card.
5. `histos status`, or the desktop app's overview, any time you want the full picture.

(Vaults created before Histos's English rename use `propuestas/`/`aprobados/` instead of `proposals/`/`approved/` — same folders, same behavior, just the old names. `histos` detects whichever one your vault already has and keeps using it, so existing vaults are never migrated automatically.)

## Why it's safe to leave it working unsupervised

The agent can **never** write to `content/*.md` without going through steps 2-4 above, and can **never** touch the dependency graph without asking you first in the conversation (a rule that lives in `AGENTS.md`, and that the CLI itself enforces with the `--authorized` flag). You can leave it processing a queue of cards without being present: the worst you'll find when you get back is a few yellow cards waiting for review, with their full drafts already visible in `proposals/` — never a surprise written without your permission. Reviewing those later from the terminal or from the desktop app makes no difference to any of this.

## Other useful commands

These are what the agent runs for itself — terminal/agent-only, no desktop app screen for them, since a human doesn't normally run these by hand:

- `histos describe <id> [--text "..."] [--sources path1 path2 ...]` — sets or changes a card's description and/or its list of external reference files (`.txt`, `.md`, `.tex`, `.docx` — e.g. the Word doc where you keep your bibliography, or the `.tex` you're editing in VSCode/Overleaf). Paths can be anywhere on disk, they don't have to live inside the vault. `--sources` replaces the whole list, it doesn't append. It doesn't touch content, so it doesn't need approval.
- `histos context <id>` — bundles into a single block of text: the card's description and sources, the same for each direct dependency (plus its content if already Approved), and `PROJECT.md` if it exists. `AGENTS.md` tells the agent to run this before drafting an assigned card.

**Important — this isn't automatic by location.** Having a Word doc or a `.tex` file open in the same vault folder doesn't make it get used automatically: you have to register the path explicitly once with `describe --sources`. That's deliberate — without that registration there's no reliable way to know which loose file is relevant to which card. Register explicitly once, use it automatically (via `context`) afterward.
- `histos link <id> --depends-on ID [ID...] --authorized` — for when you discover a dependency after already creating the card (if you knew it from the start, just set it directly in `add-card --depends-on`).

## If something looks off

```bash
histos validate
```

Validates `project.canvas` against the formal schema and tells you exactly what's wrong if something got corrupted (broken references, dependency cycles, malformed cards). Terminal-only — there's no desktop app screen for this one either.

## Full reference

All 12 commands with their flags are in the [README's CLI section](../README.md#cli). The formal `.canvas` schema is in [docs/canvas-schema.md](canvas-schema.md). The desktop app doesn't need a separate reference — every screen is just the 5 actions from the table above, and the buttons are labeled for what they do.
