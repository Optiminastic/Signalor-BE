"""
The fix agent: a bounded tool-loop that turns ONE analyzer finding into a set of
file edits for a connected repo. The LLM (Claude Sonnet 4.5 via OpenRouter) gets
read-only repo tools — list_tree / read_file / search_code — explores until it
knows what to change, then calls propose_changes (full file contents) or
cannot_fix. Pure validation guards reject unsafe patches.

The result feeds the existing orchestrator (branch + commit + PR). Generation is
deliberately read-only here; nothing is written to GitHub from this module.
"""

import json
import logging

from apps.analyzer.pipeline.llm import ask_llm_with_tools

from .auth import GITHUB_API
from .fixers import FileEdit, FixResult

logger = logging.getLogger("apps")

# Loop / safety bounds
MAX_STEPS = 14  # tool-call rounds — Sonnet explores several files before it proposes
MAX_FILES_READ = 20
MAX_EDIT_FILES = 6
MAX_FILE_CHARS = 14000  # truncate big files fed to the model
MAX_CONTENT_CHARS = 100_000  # reject absurd proposed file bodies
MAX_TREE_PATHS = 400


def _tools() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "list_tree",
                "description": "List file paths in the repo. Optionally filter by a path prefix like 'app/' or 'src/'.",
                "parameters": {
                    "type": "object",
                    "properties": {"prefix": {"type": "string"}},
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read the full text of a file in the repo.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_code",
                "description": "Search the repo's code for a keyword/string. Returns matching file paths.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "propose_changes",
                "description": (
                    "Submit the final file edits that fix the issue. Provide the COMPLETE new "
                    "content for each file (not a diff). Keep changes minimal and focused."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                        "edits": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string"},
                                    "new_content": {"type": "string"},
                                    "summary": {"type": "string"},
                                },
                                "required": ["path", "new_content"],
                            },
                        },
                    },
                    "required": ["edits"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "cannot_fix",
                "description": "Use when the issue cannot be fixed by editing this repo, or you lack the context to do it safely.",
                "parameters": {
                    "type": "object",
                    "properties": {"reason": {"type": "string"}},
                    "required": ["reason"],
                },
            },
        },
    ]


def _system_prompt(profile: dict) -> str:
    framework = profile.get("framework", "unknown")
    layout = profile.get("layout_path", "")
    return (
        "You are a senior frontend engineer. You fix ONE GEO/SEO (AI-visibility) issue at a time "
        f"by editing files in a {framework} repository. The app-router root layout is at "
        f"'{layout or 'unknown'}'.\n\n"
        "Workflow: use list_tree / read_file / search_code to find exactly which file(s) cause the "
        "issue, then call propose_changes with the COMPLETE new content of each file you change. "
        "Rules:\n"
        "- BE DECISIVE. You have a limited step budget. Most fixes need only 1–3 file reads — as "
        "soon as you know the change, STOP exploring and call propose_changes. Do not read more "
        "files 'to be thorough'.\n"
        "- Many fixes are new files (e.g. `app/sitemap.ts`, `app/robots.ts`) that need almost no "
        "exploration — confirm the file doesn't already exist, then create it.\n"
        "- Make the smallest change that fixes the issue. Do NOT reformat or touch unrelated code.\n"
        "- Always return full file contents, never diffs or ellipses.\n"
        "- Prefer idiomatic framework solutions (e.g. Next.js app-router `metadata`, `app/sitemap.ts`, "
        '`app/robots.ts`, JSON-LD via a <script type="application/ld+json"> in the layout).\n'
        "- REVIEW first: decide whether this is genuinely fixable by editing code. Many findings are "
        "(structure, markup, metadata, schema, internal links, components, wiring up existing content). "
        "If it is, fix it.\n"
        "- NEVER fabricate facts. Do NOT invent expert quotes, statistics, research figures, citations, "
        "testimonials, author names/bios, dates, or links to external sources you cannot verify from the "
        "repo. If a faithful fix would require real-world information that isn't already in the codebase, "
        "call cannot_fix with a one-line reason — it stays a manual task for the user. A wrong/made-up "
        "fact is worse than leaving it manual.\n"
        "- If the fix is already present, or it can't be done by editing the repo, call cannot_fix.\n"
        "- Read a file before you edit it. Don't invent file paths."
    )


def _finding_prompt(finding: dict, run) -> str:
    return (
        f"Fix this issue for the site {getattr(run, 'url', '')} "
        f"(brand: {getattr(run, 'brand_name', '') or 'n/a'}).\n\n"
        f"Finding code: {finding.get('finding_code')}\n"
        f"Pillar: {finding.get('pillar')}\n"
        f"Title: {finding.get('title')}\n"
        f"Why it matters: {finding.get('description')}\n"
        f"Suggested action:\n{finding.get('action')}\n"
    )


# --------------------------------------------------------------------------- #
# tool dispatch (read-only; backed by the GitHub client)
# --------------------------------------------------------------------------- #
def _get_tree_cached(client, profile, state) -> list[str]:
    if state.get("_tree") is None:
        branch = profile.get("default_branch") or "main"
        state["_tree"] = client.get_tree(branch)
    return state["_tree"]


def _search_code(client, query: str) -> list[str]:
    """GitHub code search scoped to the repo; falls back to filename match on the tree."""
    try:
        resp = client.session.get(
            f"{GITHUB_API}/search/code",
            params={"q": f"{query} repo:{client.repo}", "per_page": 10},
            timeout=20,
        )
        if resp.status_code == 200:
            return [item["path"] for item in resp.json().get("items", [])]
    except Exception as exc:  # noqa: BLE001
        logger.debug("code search failed (%s); falling back to tree match", exc)
    return []


def dispatch_tool(name: str, args: dict, client, profile: dict, state: dict) -> str:
    """Execute one read-only tool call and return a string result for the model."""
    if name == "list_tree":
        paths = _get_tree_cached(client, profile, state)
        prefix = (args.get("prefix") or "").strip()
        if prefix:
            paths = [p for p in paths if p.startswith(prefix)]
        clipped = paths[:MAX_TREE_PATHS]
        more = "" if len(paths) <= MAX_TREE_PATHS else f"\n…({len(paths) - MAX_TREE_PATHS} more)"
        return "\n".join(clipped) + more if clipped else "(no files)"

    if name == "read_file":
        if state["files_read"] >= MAX_FILES_READ:
            return "ERROR: file-read budget exhausted. Decide now: propose_changes or cannot_fix."
        path = (args.get("path") or "").strip()
        branch = profile.get("default_branch") or "main"
        f = client.get_file(path, ref=branch)
        if not f:
            return f"ERROR: file not found: {path}"
        state["files_read"] += 1
        text = f["text"]
        if len(text) > MAX_FILE_CHARS:
            return text[:MAX_FILE_CHARS] + f"\n…(truncated, {len(text) - MAX_FILE_CHARS} more chars)"
        return text

    if name == "search_code":
        hits = _search_code(client, (args.get("query") or "").strip())
        if not hits:  # fallback: filename match against the tree
            q = (args.get("query") or "").lower()
            hits = [p for p in _get_tree_cached(client, profile, state) if q and q in p.lower()][:10]
        return "\n".join(hits) if hits else "(no matches)"

    return f"ERROR: unknown tool {name}"


# --------------------------------------------------------------------------- #
# validation (pure-ish: only get_file for sha lookup)
# --------------------------------------------------------------------------- #
def validate_edits(raw_edits: list[dict], client, branch: str) -> tuple[list[FileEdit], str | None]:
    """Turn proposed edits into FileEdits, or return (partial, error) on any guard failure."""
    if not raw_edits:
        return [], "No edits provided."
    if len(raw_edits) > MAX_EDIT_FILES:
        return [], f"Too many files ({len(raw_edits)} > {MAX_EDIT_FILES}). Make a smaller, focused change."

    out: list[FileEdit] = []
    for e in raw_edits:
        path = (e.get("path") or "").strip().lstrip("/")
        content = e.get("new_content")
        if not path or ".." in path:
            return [], f"Invalid path: {e.get('path')!r}"
        if not isinstance(content, str) or not content.strip():
            return [], f"Empty content for {path}."
        if len(content) > MAX_CONTENT_CHARS:
            return [], f"File {path} is implausibly large; aborting."
        if path.endswith(".json"):
            try:
                json.loads(content)
            except ValueError:
                return [], f"{path} is not valid JSON."

        existing = client.get_file(path, ref=branch)
        if existing:
            # Mass-deletion guard: a tiny replacement of a substantial file is likely a truncation.
            orig = existing["text"]
            if len(orig) > 200 and len(content) < 0.3 * len(orig):
                return (
                    [],
                    f"Refusing to shrink {path} from {len(orig)} to {len(content)} chars (likely truncated).",
                )
            sha = existing["sha"]
        else:
            sha = None
        out.append(FileEdit(path, content, (e.get("summary") or f"Update {path}").strip(), sha))
    return out, None


# --------------------------------------------------------------------------- #
# the loop
# --------------------------------------------------------------------------- #
def generate_edits(finding: dict, client, profile: dict, run) -> dict:
    """Run the bounded agent loop for one finding.

    Returns {"result": FixResult|None, "reasoning": str, "cannot_fix": str|None}.
    """
    messages = [
        {"role": "system", "content": _system_prompt(profile)},
        {"role": "user", "content": _finding_prompt(finding, run)},
    ]
    return _run_loop(messages, client, profile, finding.get("finding_code") or "")


def repair_edits(
    finding_codes: list[str], current_edits: list[FileEdit], errors: str, client, profile: dict
) -> dict:
    """Second pass: the proposed edits failed the project's type-check/build. Give the
    model the failing files + the error output and let it read more code (e.g. type
    definitions) to produce corrected edits.

    Returns the same shape as ``generate_edits``.
    """
    files_block = "\n\n".join(f"--- {e.path} ---\n{e.new_content[:MAX_FILE_CHARS]}" for e in current_edits)
    code = finding_codes[0] if finding_codes else ""
    user = (
        f"The edits below were proposed to fix {', '.join(finding_codes) or 'a finding'}, but the "
        "project's type-check/build FAILED. Fix the errors.\n\n"
        "Files you changed:\n"
        f"{files_block}\n\n"
        "Build / type-check errors:\n"
        f"```\n{errors[:6000]}\n```\n\n"
        "Read whatever you need (e.g. the type/model/interface definitions) to find the correct "
        "field names, imports, and APIs — do NOT assume them. Then call propose_changes with the "
        "COMPLETE corrected content of every file the fix needs, or cannot_fix if it can't be done."
    )
    messages = [
        {"role": "system", "content": _system_prompt(profile)},
        {"role": "user", "content": user},
    ]
    return _run_loop(messages, client, profile, code)


def _run_loop(messages: list[dict], client, profile: dict, finding_code: str) -> dict:
    """Drive the bounded tool-loop given a seeded message list. Shared by
    generate_edits (first pass) and repair_edits (build-error second pass)."""
    branch = profile.get("default_branch") or "main"
    tools = _tools()
    state = {"files_read": 0, "_tree": None}
    reasoning_bits: list[str] = []
    retried = False

    for step in range(MAX_STEPS):
        steps_left = MAX_STEPS - step
        force_decision = steps_left <= 1
        out = ask_llm_with_tools(
            messages,
            tools,
            preferred_provider="sonnet",
            max_tokens=8000,
            purpose=f"github.agent.{finding_code}",
        )
        if out["finish_reason"] in ("no_key", "error"):
            return {"result": None, "reasoning": "", "cannot_fix": "LLM unavailable for the fix agent."}

        if out["text"]:
            reasoning_bits.append(out["text"])
        calls = out["tool_calls"]
        if not calls:
            # Model answered without a tool call — nudge it to decide.
            messages.append(out["message"])
            messages.append({"role": "user", "content": "Call propose_changes or cannot_fix to finish."})
            continue

        messages.append(out["message"])  # assistant message carrying the tool_calls

        terminal = None
        for call in calls:
            name, args, cid = call["name"], call["arguments"], call["id"]
            if name == "cannot_fix":
                return {
                    "result": None,
                    "reasoning": "\n".join(reasoning_bits),
                    "cannot_fix": args.get("reason") or "Agent could not fix this finding.",
                }
            if name == "propose_changes":
                edits, err = validate_edits(args.get("edits") or [], client, branch)
                if err and not retried:
                    retried = True
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": cid,
                            "content": f"REJECTED: {err} Please revise and call propose_changes again.",
                        }
                    )
                    terminal = "retry"
                    continue
                if err:
                    return {"result": None, "reasoning": "\n".join(reasoning_bits), "cannot_fix": err}
                result = FixResult(edits=edits, applied=[finding_code], skipped=[])
                summary = args.get("summary") or ""
                if summary:
                    reasoning_bits.append(summary)
                return {"result": result, "reasoning": "\n".join(reasoning_bits), "cannot_fix": None}

            # read-only tool
            content = dispatch_tool(name, args, client, profile, state)
            if force_decision:
                content += "\n(Final step — call propose_changes or cannot_fix NOW; no more reads.)"
            elif steps_left <= 3:
                content += (
                    f"\n(Only {steps_left} steps left — stop exploring and call propose_changes "
                    "with your best fix, or cannot_fix.)"
                )
            messages.append({"role": "tool", "tool_call_id": cid, "content": content})

        if terminal == "retry":
            continue

    # Out of exploration budget — one last forced decision (terminal tools only).
    messages.append(
        {
            "role": "user",
            "content": (
                "You are out of exploration budget. Do NOT call list_tree, read_file, or "
                "search_code. Based on what you've already seen, call propose_changes with your "
                "best fix now, or cannot_fix if it genuinely can't be done by editing this repo."
            ),
        }
    )
    out = ask_llm_with_tools(
        messages,
        tools,
        preferred_provider="sonnet",
        max_tokens=8000,
        purpose=f"github.agent.{finding_code}.final",
    )
    if out["text"]:
        reasoning_bits.append(out["text"])
    for call in out["tool_calls"]:
        if call["name"] == "cannot_fix":
            return {
                "result": None,
                "reasoning": "\n".join(reasoning_bits),
                "cannot_fix": call["arguments"].get("reason") or "Agent could not fix this finding.",
            }
        if call["name"] == "propose_changes":
            edits, err = validate_edits(call["arguments"].get("edits") or [], client, branch)
            if not err and edits:
                if call["arguments"].get("summary"):
                    reasoning_bits.append(call["arguments"]["summary"])
                return {
                    "result": FixResult(edits=edits, applied=[finding_code], skipped=[]),
                    "reasoning": "\n".join(reasoning_bits),
                    "cannot_fix": None,
                }
            return {"result": None, "reasoning": "\n".join(reasoning_bits), "cannot_fix": err}

    return {
        "result": None,
        "reasoning": "\n".join(reasoning_bits),
        "cannot_fix": "Agent did not converge on a fix within the step budget.",
    }
