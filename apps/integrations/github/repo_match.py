"""Pick which granted repo a brand's fix PRs should target.

Why this exists
---------------
When the GitHub App is installed on "All repositories" an installation can grant
dozens of repos. The callback used to take ``repositories[0]`` — whatever order
GitHub's API happened to return — so a fix PR could land on a completely
unrelated repository.

This module scores each granted repo against the brand's own website and returns
the best match, but only when the evidence is strong and unambiguous. With weak
or tied evidence it returns no repo and ``confident=False``, so the product asks
the user to choose rather than letting the agent guess. Guessing wrong means
opening a PR on someone's unrelated repository, which is not a recoverable
mistake in the way a missing suggestion is.

Pure module: no network, no DB, no Django imports. The caller supplies the repo
dicts (straight from ``auth.list_installation_repos``) and the brand's host.
"""

from __future__ import annotations

import re

# Score floor for an automatic pick. Below this the caller must ask the user.
CONFIDENT_SCORE = 60

# The winner must beat the runner-up by this much, otherwise two repos look
# equally plausible (e.g. "acme" and "acme-web") and picking either is a coin flip.
MIN_MARGIN = 15

# Host labels that never identify a brand, so they must not be matched against a
# repo name. Without this, "www.acme.com" would try to match a repo called "www".
_GENERIC_HOST_LABELS = frozenset(
    {
        "www",
        "app",
        "web",
        "shop",
        "store",
        "blog",
        "docs",
        "com",
        "org",
        "net",
        "io",
        "ai",
        "co",
        "uk",
        "dev",
        "xyz",
        "me",
        "info",
        "biz",
        "us",
        "in",
    }
)


def _slug(value: str) -> str:
    """Lowercase alphanumeric form, so "Better-Versus" and "BetterVersus" match."""
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _host(value: str) -> str:
    """Bare lowercase host from a URL or host string (no scheme, www, port, path)."""
    s = (value or "").strip().lower()
    if not s:
        return ""
    s = re.sub(r"^[a-z][a-z0-9+.-]*://", "", s)
    s = s.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    s = s.split(":", 1)[0]
    return s[4:] if s.startswith("www.") else s


def _host_tokens(host: str) -> set[str]:
    """Identifying slugs for a host: each meaningful label plus the whole host.

    ``betterversus.com`` -> {"betterversus", "betterversuscom"}. Generic labels
    such as "www" or the TLD are dropped so they cannot match a repo by accident.
    """
    host = _host(host)
    if not host:
        return set()
    labels = [label for label in host.split(".") if label]
    tokens = {_slug(label) for label in labels if label not in _GENERIC_HOST_LABELS}
    tokens.add(_slug(host))
    return {token for token in tokens if token}


def _homepage_matches(repo: dict, brand_host: str) -> bool:
    """The repo's own Website field points at the brand's domain (or a subdomain)."""
    home = _host(repo.get("homepage") or "")
    host = _host(brand_host)
    if not home or not host:
        return False
    return home == host or home.endswith("." + host) or host.endswith("." + home)


def score_repo(repo: dict, brand_host: str, brand_name: str = "") -> tuple[int, str]:
    """Score one repo against a brand. Returns ``(score, human reason)``.

    A negative score means "never pick this automatically".
    """
    if repo.get("archived"):
        return -1, "archived"

    name_slug = _slug(repo.get("name") or "")
    tokens = _host_tokens(brand_host)
    brand_slug = _slug(brand_name)

    score = 0
    reasons: list[str] = []

    if _homepage_matches(repo, brand_host):
        score += 100
        reasons.append("its GitHub Website field points at the brand domain")

    if name_slug and name_slug in tokens:
        score += 60
        reasons.append("the repo name matches the brand domain")
    elif name_slug and any(
        len(name_slug) >= 4 and (name_slug in token or token in name_slug) for token in tokens
    ):
        score += 30
        reasons.append("the repo name overlaps the brand domain")

    if brand_slug and name_slug and brand_slug == name_slug:
        score += 45
        reasons.append("the repo name matches the brand name")

    description = (repo.get("description") or "").lower()
    if description and any(token and token in _slug(description) for token in tokens):
        score += 25
        reasons.append("the description mentions the brand")

    if repo.get("fork"):
        score -= 25
        reasons.append("it is a fork")

    return score, ", ".join(reasons) or "no link to the brand domain"


def pick_repo(repos: list[dict], brand_host: str, brand_name: str = "") -> dict:
    """Choose the repo a brand's fix PRs should target.

    Returns a JSON-serializable record of the decision:

    ``repo_full_name`` — the pick, or "" when the caller must ask the user.
    ``confident``      — whether the pick is safe to apply automatically.
    ``reason``         — why, in words meant for the user.
    ``candidates``     — the top few scores, so a wrong pick can be debugged.
    """
    scored: list[tuple[int, str, str]] = []
    for repo in repos or []:
        full_name = repo.get("full_name") or ""
        if not full_name:
            continue
        score, reason = score_repo(repo, brand_host, brand_name)
        scored.append((score, full_name, reason))

    scored.sort(key=lambda row: (-row[0], row[1]))
    candidates = [{"repo": name, "score": score, "why": why} for score, name, why in scored[:5]]

    if not scored:
        return {
            "repo_full_name": "",
            "confident": False,
            "reason": "The installation granted no repositories.",
            "candidates": [],
        }

    best_score, best_name, best_reason = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else None

    if best_score < CONFIDENT_SCORE:
        return {
            "repo_full_name": "",
            "confident": False,
            "reason": (
                "No granted repository is clearly this brand's website, "
                "so the repository needs to be chosen manually."
            ),
            "candidates": candidates,
        }

    if runner_up is not None and best_score - runner_up < MIN_MARGIN:
        return {
            "repo_full_name": "",
            "confident": False,
            "reason": (
                f"{best_name} and {scored[1][1]} look equally likely, "
                "so the repository needs to be chosen manually."
            ),
            "candidates": candidates,
        }

    return {
        "repo_full_name": best_name,
        "confident": True,
        "reason": f"Matched {best_name} because {best_reason}.",
        "candidates": candidates,
    }
