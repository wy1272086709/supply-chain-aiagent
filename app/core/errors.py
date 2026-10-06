"""Nest 风格异常 — 错误体 {"statusCode", "message", "error"}，中文文案逐字保留。"""

from __future__ import annotations

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

NEST_ERROR_NAMES = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    409: "Conflict",
    413: "Payload Too Large",
    422: "Unprocessable Entity",
    500: "Internal Server Error",
    503: "Service Unavailable",
}


class AppError(Exception):
    status_code = 500

    def __init__(self, message: str = "Internal server error", status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code

    def to_response(self) -> JSONResponse:
        return JSONResponse(
            status_code=self.status_code,
            content={
                "statusCode": self.status_code,
                "message": self.message,
                "error": NEST_ERROR_NAMES.get(self.status_code, "Internal Server Error"),
            },
        )


class BadRequestError(AppError):
    status_code = 400


class UnauthorizedError(AppError):
    status_code = 401


class ForbiddenError(AppError):
    status_code = 403


class NotFoundError(AppError):
    status_code = 404


class ConflictError(AppError):
    status_code = 409


class PayloadTooLargeError(AppError):
    status_code = 413


class ServiceUnavailableError(AppError):
    status_code = 503


def _validation_message(exc: RequestValidationError) -> list[str]:
    messages: list[str] = []
    for err in exc.errors():
        loc = [str(part) for part in err.get("loc", []) if part != "body"]
        field = ".".join(loc) if loc else ""
        err_type = err.get("type", "")
        if err_type == "extra_forbidden":
            name = loc[-1] if loc else "property"
            messages.append(f"property {name} should not exist")
        elif err_type == "missing":
            messages.append(f"{field} should not be empty" if field else "property should not be empty")
        elif err_type in ("int_parsing", "int_type"):
            messages.append(f"{field} must be an integer number" if field else "value must be an integer number")
        elif err_type in ("string_type",):
            messages.append(f"{field} must be a string" if field else "value must be a string")
        elif err_type in ("bool_type", "bool_parsing"):
            messages.append(f"{field} must be a boolean value" if field else "value must be a boolean value")
        else:
            msg = err.get("msg", "invalid value")
            messages.append(f"{field}: {msg}" if field else msg)
    return messages or ["Invalid request"]


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return exc.to_response()


async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "statusCode": 400,
            "message": _validation_message(exc),
            "error": "Bad Request",
        },
    )


async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"statusCode": 500, "message": "Internal server error", "error": "Internal Server Error"},
    )
