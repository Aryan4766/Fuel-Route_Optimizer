"""Domain exceptions and the DRF exception handler that turns them into JSON errors."""
import logging

from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


class ServiceError(Exception):
    """Base class: every subclass maps to one HTTP status and a stable machine-readable code."""

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    code = "internal_error"

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class LocationNotFound(ServiceError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "location_not_found"


class LocationOutOfArea(ServiceError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "location_out_of_area"


class NoRouteFound(ServiceError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "no_route"


class InfeasibleRoute(ServiceError):
    """No sequence of known fuel stations lets the vehicle complete the route."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "no_feasible_fuel_plan"


class UpstreamTimeout(ServiceError):
    status_code = status.HTTP_504_GATEWAY_TIMEOUT
    code = "upstream_timeout"


class UpstreamError(ServiceError):
    status_code = status.HTTP_502_BAD_GATEWAY
    code = "upstream_error"


class StationDataUnavailable(ServiceError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "station_data_unavailable"


def _error_body(code: str, message: str, details=None) -> dict:
    body = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return body


def api_exception_handler(exc, context):
    """Render every failure as {"error": {"code", "message", "details"?}}."""
    if isinstance(exc, ServiceError):
        log = logger.error if exc.status_code >= 500 else logger.info
        log("%s: %s", exc.code, exc.message)
        return Response(_error_body(exc.code, exc.message, exc.details), status=exc.status_code)

    response = exception_handler(exc, context)
    if response is None:
        logger.exception("Unhandled error")
        return Response(
            _error_body("internal_error", "An unexpected error occurred."),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if isinstance(exc, ValidationError):
        body = _error_body("validation_error", "Request validation failed.", response.data)
    elif isinstance(exc, APIException):
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        body = _error_body(exc.get_codes() if isinstance(exc.get_codes(), str) else "error", detail)
    else:
        body = _error_body("error", str(exc))
    response.data = body
    return response
