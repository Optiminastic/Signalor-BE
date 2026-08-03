"""How a Signalor fix PR presents itself: title, commit messages, labels, body.

Pure module — no Django, no network. The orchestrator gathers the facts into a
``FixContext`` and everything here is a string transformation, so the conventions
are unit-testable without a repo or an installation.

Two things this deliberately does NOT do:

* It never puts the agent's raw chain-of-thought at the top of the body. That
  transcript ("Let me look at the homepage file… Now let me check…") is useful
  when a reviewer disagrees with a change, and noise the other 95% of the time,
  so it goes in a collapsed ``<details>`` at the bottom.
* It never claims a finding was fixed beyond listing what actually changed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Conventional-commit scope per analyzer pillar. The scope is what a reviewer
# scans first in a commit list, so it names the area of the site, not our
# internal pillar id.
_SCOPE_BY_PILLAR = {
    "content": "content",
    "schema": "schema",
    "eeat": "content",
    "technical": "seo",
    "entity": "seo",
    "ai_visibility": "geo",
}
_DEFAULT_SCOPE = "geo"

# Labels every Signalor PR carries, so they can be filtered/automated on.
_BASE_LABELS = ["signalor", "geo"]

_MAX_TITLE = 72  # conventional-commit soft limit; GitHub truncates well past this


@dataclass
class FixContext:
    """Everything the PR presentation needs, already resolved from the DB."""

    site_url: str
    pillar: str = ""
    headline: str = ""
    finding_codes: list[str] = field(default_factory=list)
    #: Deep link back to the task in the dashboard ("" when it can't be resolved).
    task_url: str = ""
    #: Human label for that task, e.g. "Product demo shows fake data".
    task_label: str = ""
    #: True for a Content-Optimisation PR rather than a GEO finding fix.
    is_content: bool = False


def scope_for(pillar: str) -> str:
    return _SCOPE_BY_PILLAR.get((pillar or "").strip().lower(), _DEFAULT_SCOPE)


def _subject(text: str) -> str:
    """Conventional-commit subject: lowercase first letter, no trailing period."""
    subject = (text or "").strip().rstrip(".")
    if not subject:
        return ""
    return subject[0].lower() + subject[1:]


def commit_message(ctx: FixContext, summary: str) -> str:
    """One file's commit, e.g. ``fix(content): add 'Demo data' labels to metrics``."""
    kind = "docs" if ctx.is_content else "fix"
    subject = _subject(summary) or "apply Signalor fix"
    return f"{kind}({scope_for(ctx.pillar)}): {subject}"[:_MAX_TITLE]


def pr_title(ctx: FixContext) -> str:
    """PR title in the same conventional form as the commits."""
    if ctx.is_content:
        return f"docs({scope_for(ctx.pillar)}): {_subject(ctx.headline) or 'update page content'}"[
            :_MAX_TITLE
        ]
    subject = _subject(ctx.headline) or _subject(", ".join(ctx.finding_codes)) or "apply GEO fixes"
    return f"fix({scope_for(ctx.pillar)}): {subject}"[:_MAX_TITLE]


def labels_for(ctx: FixContext) -> list[str]:
    """Labels to apply to the PR, de-duplicated and order-stable."""
    labels = [*_BASE_LABELS, scope_for(ctx.pillar)]
    if ctx.is_content:
        labels.append("content")
    seen: set[str] = set()
    return [x for x in labels if not (x in seen or seen.add(x))]


def _fixes_line(ctx: FixContext) -> list[str]:
    """Sentry-style backlink so the PR says which task it closes."""
    if not ctx.task_url:
        return []
    label = ctx.task_label or "the Signalor task"
    return [f"Fixes [{label}]({ctx.task_url})", ""]


def _changes_table(changes: list[tuple[str, str]]) -> list[str]:
    """A table reads better than a bullet list once there is more than one file."""
    if not changes:
        return []
    rows = ["| File | Change |", "| --- | --- |"]
    for path, summary in changes:
        # Escape pipes so a summary containing one can't break the table.
        cell = (summary or "").replace("|", "\\|")
        rows.append(f"| `{path}` | {cell} |")
    return ["### What changed", "", *rows, ""]


def _reasoning_block(reasoning: str) -> list[str]:
    """The agent's working, collapsed — available on demand, not in the way."""
    if not reasoning.strip():
        return []
    return [
        "<details>",
        "<summary>Agent reasoning</summary>",
        "",
        reasoning.strip()[:4000],
        "",
        "</details>",
        "",
    ]


def _copy_changes(edits: list[dict] | None) -> list[str]:
    """Requested before/after copy, so a reviewer can check wording without a diff."""
    if not edits:
        return []
    rows: list[str] = []
    for edit in edits:
        if edit.get("kind") == "metadata":
            rows.append(f"- **{edit.get('field', 'title')}** → {str(edit.get('new', ''))[:200]}")
        else:
            before = str(edit.get("original", ""))[:80]
            rows.append(f"- {before} → {str(edit.get('new', ''))[:120]}")
    return ["### Copy changes", "", *rows, ""]


def pr_body(
    ctx: FixContext,
    changes: list[tuple[str, str]],
    skipped: list[str] | None = None,
    reasoning: str = "",
    content_edits: list[dict] | None = None,
) -> str:
    """The full PR description, ordered so a reviewer can act on the first screen."""
    intro = (
        "Content edits requested from Signalor's Content Optimisation."
        if ctx.is_content
        else "This raises the GEO / AI-visibility score for the site below."
    )
    lines: list[str] = [
        f"**{ctx.headline}**" if ctx.headline else "**Signalor auto-fix**",
        "",
        intro,
        f"Site: {ctx.site_url}",
        "",
        *_fixes_line(ctx),
        *_copy_changes(content_edits),
        *_changes_table(changes),
    ]
    if ctx.finding_codes:
        lines += [
            "### Findings addressed",
            "",
            *[f"- `{code}`" for code in ctx.finding_codes],
            "",
        ]
    if skipped:
        lines += [
            "### Skipped",
            "",
            "Already present, or not fixable in code: "
            + ", ".join(f"`{c}`" for c in skipped),
            "",
        ]
    lines += _reasoning_block(reasoning)
    lines += [
        "---",
        "Opened by the Signalor GitHub App. Signalor re-checks the score after merge.",
    ]
    return "\n".join(lines)
