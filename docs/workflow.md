# Working with Histos

[usage.md](usage.md) explains how each piece works; [`AGENTS.md`](../src/histos/templates/AGENTS.md) tells the agent which rules to follow. This page is for **you**: how to organize the work so Histos pays off — the decisions about size, rhythm and order that no command makes for you. Examples come from the sample vault in [`examples/sample-paper/`](../examples/sample-paper/).

## 1. The cycle in one sentence

The agent proposes, you approve — and your time goes into deciding, not into drafting or watching. Everything below is about keeping it that way: proposals you can judge quickly, decisions taken before the work that depends on them, and nothing waiting on you that doesn't need you.

## 2. Starting a project

- **Write `PROJECT.md` and `ARCHITECTURE.md` before the first card.** `PROJECT.md` says what the project is (goal, scope, audience); `ARCHITECTURE.md` says how it's written — for a thesis, structure, register and citation format; for code, stack and conventions. Every card the agent works starts from these two. Ten lines each is plenty.
- **Let the agent interview you, then correct its card table before anything is created.** Cheap to fix in a table, tedious to fix on a board.
- **Old vault?** `histos init --add-config`, and copy the current `AGENTS.md` over the old one (see [usage.md](usage.md#upgrading-a-vault-from-v2)).

## 3. Cutting the work into cards

- **One card, one proposal you can review in one sitting.** If reviewing it would take you more than a few minutes, it's two cards.
- **Decisions are cards of their own, and they come first.** A choice that other work depends on — a structure, a method, a library, a scope cut — gets a `dec-` card the others depend on. An approved decision is cheap; a decision discovered halfway through a chapter or a module means redoing it.
- **Use a type prefix in ids:** `dec-` (decision), `cod-` (code), `doc-` (docs/writing), `pru-` (a real-world test), `rel-` (release) — or `cap1`, `cap2` for chapters. The board reads at a glance, and evals can target types (`only: ["cap*"]`, `except: ["cod-*"]`).
- **Give each card a real description**, a sentence or two about what "done" means. It's what the agent works from, and it's most of what `classify` has to pick a tier: "Compare the three main approaches and their trade-offs" gets `reasoning`; "Results" gets a guess.
- **Dependencies only where they're real.** A card should depend on another only if it can't be done well without that one's approved content. Over-linking blocks work for nothing; `arrange` hides redundant arrows anyway.

## 4. The rhythm

The loop that works best — the same one Histos v3 was built with, card by card:

1. **Review everything yellow.** `histos diff <id>`: read the evals block first, then the change. Approve, or reject with concrete feedback ("add the 2024 survey", not "not good enough") — the agent gets it verbatim on its next attempt.
2. **The agent carries out what you approved.** For writing, the approved text *is* the result. For code, the approved card is the spec: the agent implements it, runs the tests, and commits in small steps.
3. **The agent proposes everything that just got unblocked.**
4. Again.

Each turn leaves the board clean: nothing orange halfway, nothing purple that could have been proposed. When the board looks tangled after a batch of approvals, `histos arrange`.

## 5. Code projects: the repository lives next to the vault

The vault holds the plan; the code lives in its own repository, in a **sibling folder** — never inside the vault, which would fill Obsidian with source files:

```
Side projects/
├── My Tool/          # the Histos vault: board, cards, PROJECT.md
└── My-Tool/          # the git clone the agent works in
```

Use a branch for each big version and merge it when the release card is done. The card is the specification: approved first, then implemented — never the other way round.

## 6. Models and subagents

- **Read the tier's reason, then decide the model.** The tier is a suggestion: overrule it whenever you know better ("it says fast, but this abstract has to summarize the whole thesis — use the bigger model").
- **Subagents pay off for self-contained cards:** everything needed is in the card and its dependencies, nothing to ask you, and either large or able to run while the parent does another. Keep decisions with the parent. Two at a time is plenty: you still review one by one.
- **"attempt 3 of 3" in a diff** means the evals caught problems twice before this version passed. Look at that one more carefully.

## 7. Leaving it working alone

Before you go:

1. Have the agent classify the queue and propose the models.
2. Approve them all at once (one `model ... --approved ... --authorized` per model).
3. Check `histos status`: nothing under "Waiting for your model approval".

When you come back, review the yellow cards first; then the orange ones that ran out of attempts — their `eval_results` say why; then decide about the untouched ones.

## 8. Tuning the filter

- **Start with the two evals `init` gives you.** They catch direct writes and leftover placeholders, which is most of the obvious junk.
- **Add an eval when you've rejected the same problem twice**, not before. A word minimum for chapters, required headings for a template — `evals/examples.yaml` has both, commented out.
- **An eval that fails good drafts is a bad eval:** relax it or narrow it with `only`/`except`, then `histos retry` the cards it stopped.
- **Edited a card by hand?** `histos accept-edit <id>`, or every proposal fails `content_untouched` until you do.

## 9. Keeping the board readable

- `histos arrange` after a batch of approvals — it only runs when you ask, and the previous layout is kept in `project.canvas.bak`.
- When `PROJECT.md` or `ARCHITECTURE.md` should change, let the agent propose it (`guide propose`) and review it like a card. Guidance files that drift from reality mislead every card after them.

## 10. Common mistakes

- **Classifying the queue when it's created.** The tier depends on the approved content of each card's dependencies, which doesn't exist yet. Classify right before working.
- **Approving without reading the evals block.** It's the quickest signal of how hard the agent struggled.
- **Cards too big to review.** You end up skimming — and skimming is how wrong content gets approved.
- **Open decisions inside a work card.** The agent will pick something; you'll want something else after the work is done. Pull the decision out into a `dec-` card.
- **Leaving without approving the models.** The queue won't move: the agent only works cards with an approved model while you're away.
