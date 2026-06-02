"""HMAC-signed unsubscribe tokens for drip emails.

Uses Django's `signing` module (keyed off `SECRET_KEY`) so tokens are
forge-proof, self-contained (no DB lookup to validate), and don't expire
unless explicitly given a max_age. Tokens look like:

    audit-smoke-test@example.com:signature

The receiver passes the whole string back as `?token=...`; `unsign_email()`
returns the original email or raises `signing.BadSignature`.
"""
from django.conf import settings
from django.core import signing

# Salt isolates this signer from other signed payloads using the same
# SECRET_KEY — a leaked unsubscribe token can't be replayed as a password
# reset, session, or any other Django-signed payload.
_SALT = "drip-email-unsubscribe-v1"


def _signer() -> signing.Signer:
    return signing.Signer(salt=_SALT)


def sign_email(email: str) -> str:
    """Return a signed token that can be embedded in an unsubscribe URL."""
    return _signer().sign(email.lower().strip())


def unsign_email(token: str) -> str:
    """Return the email encoded in `token`, or raise `signing.BadSignature`."""
    return _signer().unsign(token)


def make_unsubscribe_url(email: str) -> str:
    """Full URL the email body / List-Unsubscribe header should point at."""
    base = settings.FRONTEND_BASE_URL.rstrip("/")
    # We host the unsubscribe handler on the BE rather than the FE so the
    # action happens in one round-trip with no FE auth check needed.
    api_base = getattr(settings, "BACKEND_BASE_URL", base.replace("3000", "8000"))
    return f"{api_base.rstrip('/')}/api/drip/unsubscribe/?token={sign_email(email)}"
