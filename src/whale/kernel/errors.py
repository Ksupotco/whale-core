"""One error model for every boundary (RPC, WS, tools, devices)."""

from __future__ import annotations

from typing import Any

CODES = {
    "invalid_params": 400,
    "unauthorized": 401,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "rate_limited": 429,
    "internal": 500,
    "unavailable": 503,
    "timeout": 504,
}


class WhaleError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        if code not in CODES:
            code = "internal"
        self.args = (message,)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}

    @property
    def http_status(self) -> int:
        return CODES[self.code]

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message, "retryable": self.retryable}
        if self.details:
            out["details"] = self.details
        return out


def not_found(what: str, ident: str) -> WhaleError:
    return WhaleError("not_found", f"{what} not found: {ident}", details={"id": ident})


def invalid(message: str, **details: Any) -> WhaleError:
    return WhaleError("invalid_params", message, details=details)
