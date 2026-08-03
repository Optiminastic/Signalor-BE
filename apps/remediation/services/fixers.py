"""
Turn analyzer finding codes into concrete file edits for a Next.js repo.

Design: the *transforms* are pure functions (string in → string out) so they're
unit-testable with no network. ``build_edits`` is the only part that touches the
GitHub client — it fetches the current file, runs the pure transform, and emits
a FileEdit. Every transform is idempotent: if the fix is already present it
returns None and the finding is reported as skipped rather than re-applied.

v1 scope: Schema (JSON-LD) + Technical (llms.txt, AI-bot rules, canonical).
"""

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

# finding_code -> short human label, and which finding codes v1 can fix as code.
SUPPORTED_FINDINGS: dict[str, str] = {
    "no_llms_txt": "Create llms.txt",
    "ai_bots_blocked": "Unblock AI crawlers in robots.txt",
    "no_jsonld": "Add JSON-LD structured data",
    "no_organization_schema": "Add Organization schema",
    "no_canonical": "Add canonical URL",
}

_LAYOUT_FINDINGS = {"no_jsonld", "no_organization_schema", "no_canonical"}

# AI crawlers we explicitly allow. Block is fenced so it's idempotent + removable.
_AI_BOTS = ["GPTBot", "Google-Extended", "anthropic-ai", "ClaudeBot", "PerplexityBot", "CCBot"]
_SIGNALOR_START = "# >>> Signalor GEO: allow AI crawlers"
_SIGNALOR_END = "# <<< Signalor GEO"


@dataclass
class FileEdit:
    path: str
    new_content: str
    summary: str
    sha: str | None = None  # existing blob sha when updating in place


@dataclass
class FixResult:
    edits: list[FileEdit] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)  # finding codes turned into edits
    skipped: list[str] = field(default_factory=list)  # supported but already-done / not-applicable


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _brand(run) -> str:
    if getattr(run, "brand_name", ""):
        return run.brand_name.strip()
    host = urlparse(run.url).netloc or run.url
    return host.replace("www.", "").split(".")[0].title() or "This site"


def _origin(url: str) -> str:
    p = urlparse(url)
    if p.scheme and p.netloc:
        return f"{p.scheme}://{p.netloc}"
    return url.rstrip("/")


def _js_string(value: str) -> str:
    """Escape a string for embedding inside a single-quoted JS string literal."""
    return (
        value.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("<", "\\u003c")  # never let '</script>' close the tag early
        .replace("\n", " ")
    )


# --------------------------------------------------------------------------- #
# pure transforms
# --------------------------------------------------------------------------- #
def build_llms_txt(brand: str, url: str) -> str:
    origin = _origin(url)
    return (
        f"# {brand}\n\n"
        f"> {brand} — {origin}\n\n"
        "## About\n"
        f"{brand} is the official site at {origin}. This file helps AI assistants "
        "(ChatGPT, Perplexity, Claude, Gemini) understand, summarize, and cite this site.\n\n"
        "## Key pages\n"
        f"- Home: {origin}/\n\n"
        "## Contact\n"
        f"- Website: {origin}/\n"
    )


def inject_ai_bot_rules(existing: str | None) -> str | None:
    """Add an allow block for AI crawlers. None if already present (idempotent)."""
    if existing and _SIGNALOR_START in existing:
        return None
    block_lines = [_SIGNALOR_START]
    for bot in _AI_BOTS:
        block_lines.append(f"User-agent: {bot}")
        block_lines.append("Allow: /")
        block_lines.append("")
    block_lines.append(_SIGNALOR_END)
    block = "\n".join(block_lines)

    if not existing or not existing.strip():
        return f"User-agent: *\nAllow: /\n\n{block}\n"
    return f"{existing.rstrip()}\n\n{block}\n"


def build_jsonld(brand: str, url: str) -> dict:
    origin = _origin(url)
    return {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Organization",
                "@id": f"{origin}/#organization",
                "name": brand,
                "url": f"{origin}/",
            },
            {
                "@type": "WebSite",
                "@id": f"{origin}/#website",
                "name": brand,
                "url": f"{origin}/",
                "publisher": {"@id": f"{origin}/#organization"},
            },
        ],
    }


def inject_jsonld_into_layout(layout_src: str, jsonld: dict) -> str | None:
    """Insert a JSON-LD <script> right after the opening <body> tag. Idempotent."""
    if "application/ld+json" in layout_src:
        return None
    match = re.search(r"<body[^>]*>", layout_src)
    if not match:
        return None
    payload = _js_string(json.dumps(jsonld, ensure_ascii=False, separators=(",", ":")))
    script = (
        "\n        <script\n"
        '          type="application/ld+json"\n'
        "          dangerouslySetInnerHTML={{ __html: '" + payload + "' }}\n"
        "        />"
    )
    idx = match.end()
    return layout_src[:idx] + script + layout_src[idx:]


def inject_canonical_metadata(layout_src: str, url: str) -> str | None:
    """Best-effort: add metadataBase + canonical into an existing `metadata` export.

    Conservative — only touches a metadata object that's already declared and has
    no alternates yet. Returns None when it can't do so safely.
    """
    if "alternates" in layout_src and "canonical" in layout_src:
        return None
    m = re.search(r"export\s+const\s+metadata(?::\s*[\w.]+)?\s*=\s*\{", layout_src)
    if not m:
        return None
    origin = _origin(url)
    insert = f'\n  metadataBase: new URL("{origin}"),\n  alternates: {{ canonical: "/" }},'
    idx = m.end()
    return layout_src[:idx] + insert + layout_src[idx:]


# --------------------------------------------------------------------------- #
# orchestration entry — fetch + transform → FileEdits
# --------------------------------------------------------------------------- #
def build_edits(client, profile: dict, run, finding_codes: list[str]) -> FixResult:
    branch = profile.get("default_branch") or "main"
    codes = [c for c in finding_codes if c in SUPPORTED_FINDINGS]
    result = FixResult()
    brand, url = _brand(run), run.url

    # 1. llms.txt (standalone file)
    if "no_llms_txt" in codes:
        if client.get_file("public/llms.txt", ref=branch):
            result.skipped.append("no_llms_txt")
        else:
            result.edits.append(
                FileEdit("public/llms.txt", build_llms_txt(brand, url), "Created public/llms.txt")
            )
            result.applied.append("no_llms_txt")

    # 2. robots.txt AI rules (standalone file)
    if "ai_bots_blocked" in codes:
        existing = client.get_file("public/robots.txt", ref=branch)
        new = inject_ai_bot_rules(existing["text"] if existing else None)
        if new is None:
            result.skipped.append("ai_bots_blocked")
        else:
            result.edits.append(
                FileEdit(
                    "public/robots.txt",
                    new,
                    "Allow AI crawlers in robots.txt",
                    existing["sha"] if existing else None,
                )
            )
            result.applied.append("ai_bots_blocked")

    # 3. layout edits (JSON-LD + canonical share one fetch → one FileEdit)
    layout_codes = [c for c in codes if c in _LAYOUT_FINDINGS]
    if layout_codes:
        _apply_layout_edits(client, profile, branch, brand, url, layout_codes, result)

    return result


def _apply_layout_edits(client, profile, branch, brand, url, layout_codes, result: FixResult):
    layout = profile.get("layout_path")
    if not layout:
        result.skipped.extend(layout_codes)
        return
    f = client.get_file(layout, ref=branch)
    if not f:
        result.skipped.extend(layout_codes)
        return

    src = f["text"]
    summaries: list[str] = []

    schema_codes = [c for c in layout_codes if c in ("no_jsonld", "no_organization_schema")]
    if schema_codes:
        new = inject_jsonld_into_layout(src, build_jsonld(brand, url))
        if new:
            src = new
            summaries.append("JSON-LD structured data")
            result.applied.extend(schema_codes)
        else:
            result.skipped.extend(schema_codes)

    if "no_canonical" in layout_codes:
        new = inject_canonical_metadata(src, url)
        if new:
            src = new
            summaries.append("canonical URL")
            result.applied.append("no_canonical")
        else:
            result.skipped.append("no_canonical")

    if summaries:
        result.edits.append(FileEdit(layout, src, f"Add {' + '.join(summaries)} to root layout", f["sha"]))
