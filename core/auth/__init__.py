"""Who is calling, verified.

``jwt``      - better-auth JWKS verification, produces a VerifiedUser.
``identity`` - the scoping seam: verified email, or the legacy field until
               REQUIRE_VERIFIED_IDENTITY is switched on.
"""

from .identity import resolve_request_email, verified_email  # noqa: F401
from .jwt import BetterAuthJWTAuthentication, VerifiedUser  # noqa: F401
