"""Verify that the credentials this deployment needs are present and live.

Five separate keys have now been fixed locally and never reached production -
OpenRouter, Serper, Google, Langfuse, and the OpenTelemetry export settings.
Every one was found the same way: a feature was quietly doing nothing, for days,
until somebody looked. The failure mode is always the same shape - a subsystem
degrades to a warning in a worker log while the API keeps returning 200.

This makes that visible in one command, at deploy time:

    python manage.py check_config            # presence only, no network
    python manage.py check_config --probe    # also call each provider
    python manage.py check_config --strict   # exit 1 on any failure (CI/deploy gate)

Values are never printed - only whether a credential exists and whether the
provider accepted it.
"""

from collections.abc import Callable
from dataclasses import dataclass

from django.core.management.base import BaseCommand

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class Check:
    name: str
    status: str
    detail: str


@dataclass
class Credential:
    """One env-var-backed dependency and what breaks without it."""

    env: str
    breaks: str
    required: bool = True
    probe: Callable[[str], tuple[bool, str]] | None = None


def _probe_serper(key: str) -> tuple[bool, str]:
    import requests

    resp = requests.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": key, "Content-Type": "application/json"},
        json={"q": "test"},
        timeout=15,
    )
    if resp.status_code == 200:
        return True, f"{len((resp.json() or {}).get('organic', []))} results"
    # 400 "Not enough credits" is the failure that actually happened in prod.
    return False, f"HTTP {resp.status_code}: {resp.text[:80]}"


def _probe_openrouter(key: str) -> tuple[bool, str]:
    import requests

    resp = requests.get(
        "https://openrouter.ai/api/v1/key",
        headers={"Authorization": f"Bearer {key}"},
        timeout=15,
    )
    return (resp.status_code == 200), f"HTTP {resp.status_code}"


def _probe_google_embeddings(key: str) -> tuple[bool, str]:
    import requests

    from apps.analyzer.pipeline.embeddings import DEFAULT_EMBED_MODEL, EMBED_DIMENSIONS

    model = DEFAULT_EMBED_MODEL.split("/")[-1]
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent?key={key}",
        json={
            "model": f"models/{model}",
            "content": {"parts": [{"text": "probe"}]},
            "outputDimensionality": EMBED_DIMENSIONS,
        },
        timeout=20,
    )
    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code}: {resp.text[:80]}"
    dims = len(((resp.json() or {}).get("embedding") or {}).get("values") or [])
    # A width mismatch is only otherwise discovered at INSERT, after paying to
    # embed a whole run.
    if dims != EMBED_DIMENSIONS:
        return False, f"returned {dims} dims, column expects {EMBED_DIMENSIONS}"
    return True, f"{dims} dims"


CREDENTIALS = [
    Credential("OPENROUTER_API_KEY", "every LLM call: prompts, competitors, tasks", probe=_probe_openrouter),
    Credential("SERPER_API_KEY", "competitor discovery and citation verification", probe=_probe_serper),
    Credential("GOOGLE_API_KEY", "embeddings: semantic search, RAG, prompt coverage", probe=_probe_google_embeddings),
    Credential("LANGFUSE_PUBLIC_KEY", "LLM cost and latency tracing", required=False),
    Credential("LANGFUSE_SECRET_KEY", "LLM cost and latency tracing", required=False),
]


class Command(BaseCommand):
    help = "Check that required credentials are configured and accepted by their provider."

    def add_arguments(self, parser):
        parser.add_argument("--probe", action="store_true", help="Call each provider to verify the key is live.")
        parser.add_argument("--strict", action="store_true", help="Exit 1 if any check fails.")

    def handle(self, *args, **options):
        import os

        checks: list[Check] = []

        for cred in CREDENTIALS:
            value = os.getenv(cred.env, "").strip()
            if not value:
                checks.append(
                    Check(cred.env, FAIL if cred.required else WARN, f"missing — {cred.breaks}")
                )
                continue
            if not (options["probe"] and cred.probe):
                checks.append(Check(cred.env, OK, "set"))
                continue
            try:
                alive, detail = cred.probe(value)
            except Exception as exc:  # noqa: BLE001 - a probe must never break the check
                checks.append(Check(cred.env, WARN, f"probe error: {exc}"))
                continue
            checks.append(
                Check(cred.env, OK if alive else FAIL, detail if alive else f"REJECTED — {detail}")
            )

        checks.extend(self._settings_checks())
        self._report(checks)

        if options["strict"] and any(c.status == FAIL for c in checks):
            raise SystemExit(1)

    def _settings_checks(self) -> list[Check]:
        """Settings that silently disable a subsystem rather than erroring."""
        import os

        from django.conf import settings

        out: list[Check] = []

        # Langfuse v4 is built on OpenTelemetry, so this one line disables it
        # however correct the keys are — the exact trap that hid it in prod.
        if os.getenv("OTEL_SDK_DISABLED", "").strip().lower() == "true":
            langfuse_keys = os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")
            out.append(
                Check(
                    "OTEL_SDK_DISABLED",
                    FAIL if langfuse_keys else WARN,
                    "true — disables the OTel SDK that Langfuse runs on. "
                    "Use OTEL_TRACES_EXPORTER=none instead.",
                )
            )

        # Fails closed, so an unset value silently rejects every delivery.
        if not getattr(settings, "GITHUB_WEBHOOK_SECRET", ""):
            out.append(Check("GITHUB_WEBHOOK_SECRET", WARN, "missing — every GitHub webhook is rejected"))

        # Enforcement without a configured verifier refuses every caller.
        if getattr(settings, "REQUIRE_VERIFIED_IDENTITY", False) and not getattr(
            settings, "BETTER_AUTH_JWKS_URL", ""
        ):
            out.append(
                Check(
                    "BETTER_AUTH_JWKS_URL",
                    FAIL,
                    "REQUIRE_VERIFIED_IDENTITY is on but no JWKS is configured — "
                    "no caller can authenticate, every scoped endpoint returns 503",
                )
            )
        return out

    def _report(self, checks: list[Check]) -> None:
        style = {OK: self.style.SUCCESS, WARN: self.style.WARNING, FAIL: self.style.ERROR}
        label = {OK: "OK  ", WARN: "WARN", FAIL: "FAIL"}
        for c in checks:
            self.stdout.write(style[c.status](f"{label[c.status]}  {c.name:<24} {c.detail}"))

        failed = sum(1 for c in checks if c.status == FAIL)
        warned = sum(1 for c in checks if c.status == WARN)
        summary = f"\n{len(checks)} checked, {failed} failed, {warned} warning(s)."
        self.stdout.write(style[FAIL if failed else (WARN if warned else OK)](summary))
