# Trust model

The rules in [`AGENTS.md`](../src/histos/templates/AGENTS.md) — never write to `content/*.md`, never pass `--authorized` without the human's yes, never run the human's commands (`approve`, `reject`, `retry`, `accept-edit`...), never touch `histos.config.yaml` or `evals/`, never run `histos arrange` unless asked — are a **prose convention**: they depend on the agent choosing to follow them. Histos backs some of them with real checks, and is honest about the rest.

## What's enforced for any agent

- **Direct writes to `content/` are detected.** Every legitimate write of a card's body (`add-card`, `approve`, the human's `accept-edit`) records a `content_hash`; the `content_untouched` eval fails every proposal while any card's body doesn't match it, whatever wrote it. It's detection, not prevention — but nothing written behind Histos's back reaches review unnoticed.
- **The vault format.** Cards can only point to files directly inside `content/`; `histos validate` and every command reject anything else (see [canvas-schema.md](canvas-schema.md)).
- **Concurrent agents can't corrupt the vault.** The lock and atomic writes hold no matter which agent runs the commands.

## Claude-Code-specific enforcement

`histos init` also installs [`.claude/settings.json`](../src/histos/templates/.claude/settings.json) with `permissions.deny` (`Write` and `Edit`) covering `content/**`, `project.canvas`, `PROJECT.md`, `ARCHITECTURE.md`, `histos.config.yaml` and `evals/**`. Verified live: it blocks `Edit`, `Write`, and even `Bash` when the command references a denied path. `describe --sources` extends this: every time a source file is registered, its absolute path gets the same rules, so registered reference material (a Word doc, a `.tex` in Overleaf) gets the same protection as `content/`. The sync is add-only — it never removes a rule, so it can't silently undo something you added by hand. Older vaults pick up new rules the next time `describe` or `init --add-config` runs there.

## Three honest limits

1. **The deny list is Claude-Code-specific** — it doesn't protect you if the agent is another tool. Codex CLI and Gemini CLI have their own permission systems, in fundamentally different formats; neither is wired up by Histos yet (see [roadmap.md](roadmap.md#future-directions)).
2. **It's tool-level, not OS-level.** The agent and the CLI run as the same OS user, so an agent that ignores its instructions *can* run `histos approve` itself. The human-in-the-loop review is the real filter, by design.
3. **`describe --sources` doesn't require authorization**, by design (it's [`AGENTS.md`](../src/histos/templates/AGENTS.md)'s "no permission needed" list) — so a misdirected agent could register a sensitive file as a "source" and read it through `histos context`. Closing it would mean requiring authorization for `describe --sources`, a workflow change bigger than a security patch.

## Why this is enough

This is a deliberate decision, not an oversight: the use case is a single user with a trusted agent that reads and follows instructions, and a human who reviews every change. It stops being sufficient if the agent isn't trustworthy (injected instructions, a misaligned model) or if several users with different interests share the same vault. A real, agent-agnostic guarantee would need OS-level separation — e.g. a container where `content/` is mounted read-only for the agent. That's **not planned**: for the way Histos is used, the human-in-the-loop review is enough, and OS sandboxing would make the tool much harder to set up.
