"""Error responses: every failure has a code, a message, and nothing sensitive."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, OperationalError

from vidsprod.errors import (
    BlockedError,
    ConflictError,
    HumanRequiredError,
    InvalidInputError,
    NotFoundError,
    TransientError,
    VidsProdError,
)
from vidsprod.logging import get_logger, redact

log = get_logger(__name__)

_STATUS = (
    (NotFoundError, 404),
    (ConflictError, 409),
    (InvalidInputError, 422),
    (BlockedError, 409),
    (HumanRequiredError, 409),
    (TransientError, 503),
)


def _body(code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": redact(details or {})}}


def install(app: FastAPI) -> None:
    @app.exception_handler(VidsProdError)
    async def _domain(request: Request, exc: VidsProdError) -> JSONResponse:
        status = next((code for cls, code in _STATUS if isinstance(exc, cls)), 500)
        return JSONResponse(_body(exc.code, exc.message, exc.details), status_code=status)

    @app.exception_handler(OperationalError)
    async def _db_down(request: Request, exc: OperationalError) -> JSONResponse:
        log.error("database unavailable", error=type(exc.orig).__name__ if exc.orig else "")
        return JSONResponse(_body("DATABASE_UNAVAILABLE", "the database is unavailable; retry shortly"),
                            status_code=503)

    @app.exception_handler(DBAPIError)
    async def _db_error(request: Request, exc: DBAPIError) -> JSONResponse:
        log.error("database error", error=type(exc.orig).__name__ if exc.orig else "")
        return JSONResponse(_body("DATABASE_ERROR", "the database rejected the operation"),
                            status_code=409 if "violat" in str(exc.orig).lower() else 500)
