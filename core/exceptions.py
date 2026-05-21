"""Project-wide error handling.

Goals:
  - Every view returns a clean JSON error shape, never a raw HTML 500.
  - Views can raise typed `AppError`s; the handler renders them.
  - Common upstream failures (requests timeouts, HTTPErrors) are caught
    here so individual views don't have to repeat try/except boilerplate.
  - Every exception logs with request path + user email + view name so
    we can debug from logs alone.

Response shape (always a dict):
    {
        "detail":      str    # human-readable, surfaceable in UI
        "code":        str    # stable machine code, e.g. "billing_required"
        "status_code": int    # mirrors HTTP status, useful for FE clients
    }
"""

from __future__ import annotations

import logging
from typing import Any

import requests
from django.core.exceptions import (
    ObjectDoesNotExist,
)
from django.core.exceptions import (
    PermissionDenied as DjangoPermissionDenied,
)
from django.core.exceptions import (
    ValidationError as DjangoValidationError,
)
from django.http import Http404
from rest_framework import status as http_status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger("apps")


class AppError(Exception):
    """Base class for application-level errors that should reach the client.

    Views can raise these directly; the handler renders them as JSON with
    the chosen status code. Subclass for domain-specific cases or use the
    factory helpers below.
    """

    status_code: int = http_status.HTTP_400_BAD_REQUEST
    default_code: str = "app_error"
    default_detail: str = "Something went wrong."

    def __init__(
        self,
        detail: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        extra: dict[str, Any] | None = None,
    ):
        self.detail = detail or self.default_detail
        self.code = code or self.default_code
        if status_code is not None:
            self.status_code = status_code
        self.extra = extra or {}
        super().__init__(self.detail)


class ValidationError(AppError):
    status_code = http_status.HTTP_400_BAD_REQUEST
    default_code = "validation_error"
    default_detail = "Invalid input."


class NotFoundError(AppError):
    status_code = http_status.HTTP_404_NOT_FOUND
    default_code = "not_found"
    default_detail = "Not found."


class ForbiddenError(AppError):
    status_code = http_status.HTTP_403_FORBIDDEN
    default_code = "forbidden"
    default_detail = "You don't have permission to do that."


class UpstreamError(AppError):
    """A third-party service we depend on returned an error. 502 is the
    correct HTTP code: we're acting as a gateway."""

    status_code = http_status.HTTP_502_BAD_GATEWAY
    default_code = "upstream_error"
    default_detail = "Upstream service failed."


class UpstreamTimeoutError(UpstreamError):
    status_code = http_status.HTTP_504_GATEWAY_TIMEOUT
    default_code = "upstream_timeout"
    default_detail = "Upstream service timed out."


class BillingRequiredError(AppError):
    """The user (or our own integration account) is out of credits/billing.
    Surface separately from generic upstream errors so the FE can prompt
    a specific action."""

    status_code = http_status.HTTP_402_PAYMENT_REQUIRED
    default_code = "billing_required"
    default_detail = "Payment is required to access this resource."


# ─── Logging helper ────────────────────────────────────────────────────────


def _request_context(context: dict[str, Any]) -> dict[str, Any]:
    """Build structured log extras from the DRF context dict."""
    request = context.get("request")
    view = context.get("view")
    extras: dict[str, Any] = {
        "view": view.__class__.__name__ if view else None,
    }
    if request is not None:
        extras["path"] = getattr(request, "path", None)
        extras["method"] = getattr(request, "method", None)
        # Best-effort user email: most public endpoints pass email as a
        # query param or in the body. Don't crash if absent.
        try:
            email = request.query_params.get("email") or (
                request.data.get("email") if hasattr(request, "data") else None
            )
            if email:
                extras["email"] = email
        except Exception:
            pass
    return extras


# ─── Exception → Response mapping ─────────────────────────────────────────


def _render(detail: str, code: str, status_code: int) -> Response:
    """Single source of truth for the JSON error envelope."""
    return Response(
        {"detail": detail, "code": code, "status_code": status_code},
        status=status_code,
    )


def _handle_app_error(exc: AppError) -> Response:
    payload: dict[str, Any] = {
        "detail": exc.detail,
        "code": exc.code,
        "status_code": exc.status_code,
    }
    if exc.extra:
        payload["extra"] = exc.extra
    return Response(payload, status=exc.status_code)


def _handle_requests_exception(exc: Exception) -> Response | None:
    """Translate `requests` library errors to clean JSON. Returns None if
    the exception isn't from `requests` so the caller can keep checking."""
    if isinstance(exc, requests.Timeout):
        return _render(
            "The upstream service didn't respond in time.",
            "upstream_timeout",
            http_status.HTTP_504_GATEWAY_TIMEOUT,
        )
    if isinstance(exc, requests.ConnectionError):
        return _render(
            "Couldn't reach the upstream service.",
            "upstream_unreachable",
            http_status.HTTP_502_BAD_GATEWAY,
        )
    if isinstance(exc, requests.HTTPError):
        # Try to extract the upstream status. 402 → billing-required so
        # the FE can show a specific message.
        upstream_status = exc.response.status_code if exc.response is not None else None
        if upstream_status == 402:
            return _render(
                "Upstream account is out of credits or has a billing problem.",
                "billing_required",
                http_status.HTTP_402_PAYMENT_REQUIRED,
            )
        return _render(
            f"Upstream service returned HTTP {upstream_status or 'error'}.",
            "upstream_error",
            http_status.HTTP_502_BAD_GATEWAY,
        )
    return None


# ─── Main DRF entrypoint ───────────────────────────────────────────────────


def custom_exception_handler(exc, context):
    """Project-wide exception handler.

    Order:
      1. AppError + subclasses → typed JSON response.
      2. Django Http404 / ObjectDoesNotExist → 404.
      3. requests.* errors → 502/504.
      4. DRF's default handler (covers DRF + auth + permission exceptions).
      5. Unknown exception → log + return 500 JSON (no HTML stacktrace).
    """
    extras = _request_context(context)

    # 1) App errors — log at warning level since they're expected outcomes.
    if isinstance(exc, AppError):
        logger.warning(
            "AppError: %s code=%s status=%s | %s",
            exc.detail,
            exc.code,
            exc.status_code,
            extras,
        )
        return _handle_app_error(exc)

    # 2) Django shortcuts — translate to JSON instead of HTML.
    if isinstance(exc, (Http404, ObjectDoesNotExist)):
        return _render("Not found.", "not_found", http_status.HTTP_404_NOT_FOUND)
    if isinstance(exc, DjangoPermissionDenied):
        return _render("Forbidden.", "forbidden", http_status.HTTP_403_FORBIDDEN)
    if isinstance(exc, DjangoValidationError):
        # Django ValidationError's message_dict / messages list isn't a
        # great client payload; join into one string.
        message = "; ".join(map(str, exc.messages)) if hasattr(exc, "messages") else str(exc)
        return _render(message, "validation_error", http_status.HTTP_400_BAD_REQUEST)

    # 3) Upstream HTTP/network errors — caught here so views don't have to
    # repeat try/except for every third-party call.
    requests_response = _handle_requests_exception(exc)
    if requests_response is not None:
        logger.warning("Upstream failure: %s | %s", exc, extras, exc_info=True)
        return requests_response

    # 4) DRF's default handler — covers APIException, NotAuthenticated,
    # PermissionDenied, MethodNotAllowed, Throttled, ParseError, etc.
    response = drf_exception_handler(exc, context)
    if response is not None:
        # Normalize shape: ensure detail + code + status_code are present.
        data = response.data if isinstance(response.data, dict) else {"detail": str(response.data)}
        data.setdefault("detail", "Request failed.")
        data.setdefault("code", _drf_code_for(exc))
        data["status_code"] = response.status_code
        response.data = data
        return response

    # 5) Unknown exception — log full traceback and return 500 JSON.
    # Critical: never let raw HTML reach an API client.
    logger.exception("Unhandled exception | %s", extras)
    return _render(
        "Something went wrong on our end. Please try again.",
        "server_error",
        http_status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def _drf_code_for(exc) -> str:
    """Stable machine codes for DRF's own exceptions."""
    name = exc.__class__.__name__
    mapping = {
        "NotAuthenticated": "not_authenticated",
        "AuthenticationFailed": "auth_failed",
        "PermissionDenied": "forbidden",
        "NotFound": "not_found",
        "MethodNotAllowed": "method_not_allowed",
        "NotAcceptable": "not_acceptable",
        "UnsupportedMediaType": "unsupported_media_type",
        "Throttled": "throttled",
        "ParseError": "parse_error",
        "ValidationError": "validation_error",
    }
    return mapping.get(name, "api_error")
