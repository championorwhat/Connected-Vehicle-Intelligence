"""Errors as RFC 9457 problem details (application/problem+json).

Every error body has the same shape, carries the request id for support, and
never includes stack traces or SQL. Unexpected exceptions become a generic 500.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("prognos_api")

TITLES = {
    400: "Bad request", 401: "Unauthorized", 403: "Forbidden", 404: "Not found",
    409: "Conflict", 422: "Validation failed", 429: "Too many requests",
    500: "Internal server error", 503: "Service unavailable",
}  # fmt: skip


class ApiError(Exception):
    def __init__(
        self, status: int, detail: str, *, code: str | None = None,
        headers: dict[str, str] | None = None, extra: dict[str, Any] | None = None,
    ) -> None:  # fmt: skip
        super().__init__(detail)
        self.status, self.detail, self.code = status, detail, code
        self.headers = headers
        self.extra: dict[str, Any] = extra or {}


def problem(
    request: Request, status: int, detail: str, *, code: str | None = None,
    headers: dict[str, str] | None = None, extra: dict[str, Any] | None = None,
) -> JSONResponse:  # fmt: skip
    body: dict[str, Any] = {
        "type": f"https://prognos.local/problems/{code or status}",
        "title": TITLES.get(status, "Error"),
        "status": status,
        "detail": detail,
        "instance": request.url.path,
        "request_id": getattr(request.state, "request_id", None),
        **(extra or {}),
    }
    return JSONResponse(body, status_code=status, headers=headers,
                        media_type="application/problem+json")  # fmt: skip


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return problem(request, exc.status, exc.detail, code=exc.code, headers=exc.headers,
                       extra=exc.extra)  # fmt: skip

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return problem(request, exc.status_code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg"), "type": e.get("type")}
            for e in exc.errors()
        ]
        return problem(request, 422, "request did not match the schema", code="validation",
                       extra={"errors": errors})  # fmt: skip

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s", request.url.path)
        return problem(request, 500, "unexpected error; quote the request_id to support")
