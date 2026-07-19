"""Protocol error envelope for the FastAPI reference adapter.

``docs/PROTOCOL.md`` § Error response shape mandates that every non-2xx
body (except ``204`` and the binary screenshot 404) carries the same
``{"error", "detail"}`` envelope. The Flask (``_error``) and Django
(``_err``) adapters have always emitted it; the FastAPI routers raised
bare :class:`fastapi.HTTPException`, which serializes to ``{"detail":
...}`` and drops the machine-readable ``error`` code entirely.

This module supplies the FastAPI equivalent.

Why a response and not an exception handler
-------------------------------------------
:mod:`bug_fab` ships ``APIRouter`` instances, never a ``FastAPI``
application — consumers mount our routers into *their* app. Exception
handlers can only be registered on an app, so a library-side
``install_error_handlers(app)`` would be one more thing a consumer must
remember to call, and forgetting it would silently restore the wrong
envelope. Returning the response directly keeps the envelope a property
of the route rather than of the consumer's wiring.
"""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

__all__ = ["protocol_error"]


def protocol_error(
    status_code: int,
    code: str,
    detail: Any,
    **extra: Any,
) -> JSONResponse:
    """Build the protocol's ``{"error", "detail", **extra}`` error envelope.

    Parameters
    ----------
    status_code:
        HTTP status for the response.
    code:
        Machine-readable token from ``docs/PROTOCOL.md`` § Standard error
        codes (``validation_error``, ``schema_error``, ``not_found``, ...).
    detail:
        Human-readable string, or a structured list such as Pydantic's
        ``exc.errors()`` for ``schema_error``.
    **extra:
        Additional top-level body fields the protocol attaches to specific
        codes — ``limit_bytes`` on ``payload_too_large``,
        ``retry_after_seconds`` on ``rate_limited``.
    """
    body: dict[str, Any] = {"error": code, "detail": detail}
    body.update(extra)
    return JSONResponse(status_code=status_code, content=body)
