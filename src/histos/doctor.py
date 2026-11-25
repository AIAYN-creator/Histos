"""Read-only vault health diagnostics (`histos doctor`).

Never acquires `.histos.lock`, never writes to the vault or `$HISTOS_HOME`, and never emits
events. Every finding reports a concise status (`ok`, `warn`, `fail`) and an actionable fix
command when something quietly disables a Histos feature.
"""
from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import __version__, canvas, checks, config, evals, events, frontmatter, operations, pauses, validation

_TEMPLATE_VERSION_RE = re.compile(r"<!--\s*histos-template-version:\s*([^\s>]+)\s*-->")


@dataclass(frozen=True)
class Finding:
    check: str
    status: str  # "ok" | "warn" | "fail"
    message: str
    fix: str | None = None

    def to_dict(self) -> dict:
        data: dict[str, str] = {"check": self.check, "status": self.status, "message": self.message}
        if self.fix:
            data["fix"] = self.fix
        return data


@dataclass(frozen=True)
class DoctorResult:
    vault_root: Path
    findings: list[Finding]

    @property
    def ok(self) -> bool:
        return not any(f.status == "fail" for f in self.findings)

    @property
    def counts(self) -> dict[str, int]:
        return {level: sum(1 for f in self.findings if f.status == level) for level in ("ok", "warn", "fail")}

    def to_dict(self) -> dict:
        return {
            "vault_root": str(self.vault_root),
            "ok": self.ok,
            "counts": self.counts,
            "findings": [f.to_dict() for f in self.findings],
        }


def _check_v3_config(vault_root: Path) -> Finding:
    missing = []
    if not config.config_path(vault_root).exists():
        missing.append(config.CONFIG_FILENAME)
    if not (vault_root / "evals").is_dir():
        missing.append("evals/")
    if not (vault_root / "evals" / "guides.yaml").exists():
        missing.append("evals/guides.yaml")
    if missing:
        return Finding("v3_config", "fail", f"missing {', '.join(missing)}", "histos init --add-config")
    return Finding("v3_config", "ok", "v3 config and evals are present")


def _check_agents_md(vault_root: Path) -> Finding:
    path = vault_root / "AGENTS.md"
    if not path.exists():
        return Finding("agents_md", "fail", "AGENTS.md is missing", "histos init (or copy src/histos/templates/AGENTS.md)")
    text = path.read_text(encoding="utf-8", errors="replace")
    match = _TEMPLATE_VERSION_RE.search(text)
    if match is None:
        return Finding(
            "agents_md", "warn",
            f"AGENTS.md has no version stamp (installed: v{__version__})",
            "copy the current src/histos/templates/AGENTS.md into this vault",
        )
    found = match.group(1)
    if found != __version__:
        return Finding(
            "agents_md", "warn",
            f"AGENTS.md is v{found}, installed Histos is v{__version__}",
            "copy the current src/histos/templates/AGENTS.md into this vault",
        )
    return Finding("agents_md", "ok", f"AGENTS.md matches installed v{__version__}")


def _check_legend(data: dict) -> Finding:
    legend = next((n for n in data.get("nodes", []) if n.get("id") == canvas.LEGEND_ID), None)
    if legend is None or legend.get("type") != "text":
        return Finding(
            "legend", "warn", "status legend node is missing in project.canvas",
            "check or restore the 'legend' text node in project.canvas",
        )
    text = legend.get("text")
    if text != canvas.LEGEND_TEXT and text not in canvas.OLDER_LEGEND_TEXTS:
        return Finding(
            "legend", "warn", "status legend text in project.canvas is unrecognized",
            "check or restore the 'legend' text node in project.canvas",
        )
    return Finding("legend", "ok", "status legend recognized")


def _read_cards_meta(vault_root: Path, data: dict) -> list[tuple[dict, dict]]:
    items: list[tuple[dict, dict]] = []
    for card in canvas.cards(data):
        md_path = canvas.card_file_path(vault_root, card)
        if not md_path.exists():
            continue
        try:
            meta, _ = frontmatter.read(md_path)
        except canvas.HistosError:
            continue
        items.append((card, meta))
    return items


def _check_unclassified(vault_root: Path, cards_meta: list[tuple[dict, dict]]) -> Finding:
    if not config.config_path(vault_root).exists():
        return Finding("unclassified_cards", "ok", "no histos.config.yaml (tiers not active)")
    missing = [card["id"] for card, meta in cards_meta
               if card.get("color") != canvas.APPROVED and not meta.get("proposed_tier")]
    if missing:
        fix = f"histos classify {' '.join(missing)}" if len(missing) <= 4 else "histos classify --all"
        return Finding("unclassified_cards", "warn", f"{len(missing)} open card(s) without proposed_tier: {', '.join(missing)}", fix)
    return Finding("unclassified_cards", "ok", "all open cards have a tier")


def _check_content_untouched(vault_root: Path, data: dict) -> Finding:
    outcome = checks.content_untouched(checks.CheckInput("", {}, vault_root, data, {}))
    if outcome.passed:
        return Finding("content_untouched", "ok", outcome.detail)
    changed = re.findall(r"content/([^\s,]+)\.md", outcome.detail)
    fix = f"histos accept-edit {' '.join(changed)}" if changed else "histos accept-edit <id>"
    return Finding("content_untouched", "fail", outcome.detail, fix)


def _check_in_progress_model(cards_meta: list[tuple[dict, dict]]) -> Finding:
    missing = [card["id"] for card, meta in cards_meta
               if card.get("color") == canvas.IN_PROGRESS and not meta.get("approved_model")]
    if missing:
        return Finding(
            "in_progress_model", "warn",
            f"{len(missing)} In progress card(s) without approved_model: {', '.join(missing)}",
            f'histos model {" ".join(missing)} --approved "<model>" --authorized',
        )
    return Finding("in_progress_model", "ok", "all In progress cards have an approved_model")


def _check_done_when(cards_meta: list[tuple[dict, dict]]) -> Finding:
    missing = [card["id"] for card, meta in cards_meta
               if card.get("color") != canvas.APPROVED and not str(meta.get(operations.DONE_WHEN_KEY) or "").strip()]
    if missing:
        return Finding(
            "done_when_missing", "warn",
            f"{len(missing)} open card(s) missing done_when: {', '.join(missing)}",
            f"add done_when to content/<id>.md and run: histos accept-edit {' '.join(missing)}",
        )
    return Finding("done_when_missing", "ok", "all open cards define done_when")


def _check_project_brief(vault_root: Path, data: dict) -> Finding:
    if canvas.cards(data) and not (vault_root / "PROJECT.md").exists():
        return Finding(
            "project_brief", "warn", "PROJECT.md is missing while the board has cards",
            "histos guide propose PROJECT.md --file <draft>",
        )
    return Finding("project_brief", "ok", "PROJECT.md present (or empty board)")


def _is_writable_dir(target: Path) -> bool:
    probe = target
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if not probe.is_dir():
        return False
    try:
        with tempfile.TemporaryFile(dir=probe):
            return True
    except OSError:
        return False


def _check_histos_home(vault_root: Path) -> Finding:
    home_dir = events.home()
    if not _is_writable_dir(home_dir):
        return Finding(
            "histos_home", "warn",
            f"$HISTOS_HOME ({home_dir}) is not writable from this environment",
            "run 'histos status' outside the sandbox or set HISTOS_HOME to a writable directory",
        )
    vault_id_path = vault_root / events.LOG_DIR / events.VAULT_ID_FILENAME
    if vault_id_path.exists():
        try:
            vault_id = json.loads(vault_id_path.read_text(encoding="utf-8")).get("id")
        except (OSError, ValueError):
            vault_id = None
        resolved = str(vault_root.resolve())
        registered = events._read_registry(home_dir / events.REGISTRY_FILENAME)
        if not any(v.get("id") == vault_id and v.get("path") == resolved for v in registered):
            return Finding(
                "histos_home", "warn",
                f"vault is not registered in {home_dir / events.REGISTRY_FILENAME}",
                "histos status",
            )
    return Finding("histos_home", "ok", f"$HISTOS_HOME ({home_dir}) is writable and up to date")


def _check_pause() -> Finding:
    paused = pauses.current()
    if paused is not None:
        since = paused.start.astimezone().strftime("%Y-%m-%d %H:%M")
        return Finding(
            "pause_active", "warn", f"review pause is active since {since}",
            "ends automatically on your next human decision (approve, reject, retry...)",
        )
    return Finding("pause_active", "ok", "no active review pause")


def diagnose(vault_root: Path) -> DoctorResult:
    findings: list[Finding] = []
    canvas_path = canvas.vault_canvas_path(vault_root)
    if not canvas_path.exists():
        findings.append(Finding("vault_valid", "fail", f"missing {canvas.CANVAS_FILENAME}", "histos init"))
        findings.append(_check_v3_config(vault_root))
        findings.append(_check_agents_md(vault_root))
        findings.append(_check_histos_home(vault_root))
        findings.append(_check_pause())
        return DoctorResult(vault_root=vault_root, findings=findings)

    try:
        data = canvas.load(vault_root)
    except (canvas.HistosError, json.JSONDecodeError, OSError) as e:
        findings.append(Finding("vault_valid", "fail", f"can't parse {canvas.CANVAS_FILENAME}: {e}", "histos validate"))
        findings.append(_check_v3_config(vault_root))
        findings.append(_check_agents_md(vault_root))
        findings.append(_check_histos_home(vault_root))
        findings.append(_check_pause())
        return DoctorResult(vault_root=vault_root, findings=findings)

    errors = validation.validate_all(data)
    if not errors:
        errors += validation.validate_frontmatter(vault_root, data)
    config_errors = config.validate(vault_root)
    errors += [f"config: {e}" for e in config_errors]
    if not config_errors:
        errors += [f"evals: {e}" for e in evals.validate(vault_root, config.load(vault_root))]
    log_errors, _ = events.check_log(vault_root)
    errors += log_errors
    if errors:
        findings.append(Finding("vault_valid", "fail", f"{len(errors)} validation issue(s): {errors[0]}", "histos validate"))
    else:
        findings.append(Finding("vault_valid", "ok", "canvas, frontmatter, config, evals and event log are valid"))

    findings.append(_check_v3_config(vault_root))
    findings.append(_check_agents_md(vault_root))
    findings.append(_check_legend(data))

    cards_meta = _read_cards_meta(vault_root, data)
    findings.append(_check_unclassified(vault_root, cards_meta))
    findings.append(_check_content_untouched(vault_root, data))
    findings.append(_check_in_progress_model(cards_meta))
    findings.append(_check_done_when(cards_meta))
    findings.append(_check_project_brief(vault_root, data))
    findings.append(_check_histos_home(vault_root))
    findings.append(_check_pause())
    return DoctorResult(vault_root=vault_root, findings=findings)
