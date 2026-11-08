# Command reference

Concrete, clearly named commands instead of having the agent hand-edit the canvas JSON, to avoid breaking the format and to limit the agent to a known set of safe operations (like an API). Agent-agnostic: works with any tool that can run shell commands and read/write files (Claude Code, Codex, Gemini CLI...).

Every command operates on the current directory, which must be the root of a Histos vault (`histos init` creates it). No command blocks on an interactive prompt, so a queue of cards can be processed in AFK mode with a simple loop in the agent that invokes the CLI — no special flag or mode needed. `histos <command> --help` has every flag.

## The agent's commands

```bash
histos status                                           # cards by status, with tier, model and worker;
                                                        # models still waiting for approval; pending guides
histos plan propose --file plan.yaml                    # the whole card table as one plan (nothing created yet)
histos plan diff                                        # the plan: cards, levels (what runs in parallel), warnings
histos add-card cap1 --title "Intro" [--description "..."] [--done-when "..."]
                                                        # standalone card -> Backlog
histos add-card cap2 --title "Cap 2" --depends-on cap1 --authorized
                                                        # --authorized is required as soon as the dependency
                                                        # graph is touched -- a flag the agent only sets after
                                                        # asking for permission in the conversation
histos link cap1 --depends-on cap0 --authorized         # adds a dependency to an ALREADY existing card
histos describe cap1 --text "..." [--sources f1 f2]     # description and/or external sources (no permission needed)
histos assign cap1 [--worker NAME] [--takeover] [--by agent|human]
                                                        # -> In progress; refuses a card someone else has
histos classify cap1 [cap2...] | --all [--dry-run]      # tier (fast/exec/reasoning) and its reason
histos model cap1 --proposed "Sonnet 5.5"               # the agent's proposal
histos model cap1 [cap2...] --approved "..." --authorized
                                                        # the human's yes, recorded
histos context cap1 [--for-subagent]                    # everything to work the card (or a subagent's brief)
histos eval cap1|PROJECT.md --file draft.md             # runs the evals on a draft, spends no attempt
histos propose cap1 --file draft.md                     # evals first; if they pass -> Proposal pending review
histos guide propose PROJECT.md --file new.md           # proposes a change to PROJECT.md / ARCHITECTURE.md
histos diff cap1                                        # evals passed + diff against the pending proposal
histos validate                                         # canvas, frontmatter, config, evals and the event log
```

## The human's commands

```bash
histos init [--add-config]                              # new vault (canvas, content/, proposals/, approved/,
                                                        # config, evals, agent instructions) -- or bring an
                                                        # existing one to v3
histos approve cap1                                     # applies the proposal to the real .md -> Approved
histos reject cap1 [--feedback "..."]                   # discards the proposal -> Backlog, feedback in status_note
histos retry cap1                                       # 3 more attempts after the evals failed 3 times
histos accept-edit cap1 [cap2...]                       # accepts your own hand edits to a card's .md
histos guide approve|reject PROJECT.md [--feedback ".."]
                                                        # a rejection's feedback goes to PROJECT.md's decisions
histos plan approve                                     # creates every card of the plan, all or nothing
histos plan reject [--feedback "..."]                   # discards it; the feedback goes to PROJECT.md's decisions
histos arrange [--no-set-aside-done] [--no-prune-redundant]
                                                        # re-lays out the WHOLE board: dependency columns, no
                                                        # arrow across a card, "Not connected" and "Done" groups,
                                                        # redundant arrows removed; old layout -> project.canvas.bak
histos --version
```

The desktop app does the human's side with buttons: [usage.md](usage.md#terminal-or-desktop-app--your-choice).
