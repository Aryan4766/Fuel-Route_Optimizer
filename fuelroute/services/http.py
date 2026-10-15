"""Shared HTTP plumbing for the external services: one session, timeouts, error mapping."""
from __future__ import annotations

import logging
from functools import lru_cache

import requests
import urllib3
from django.conf import settings
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..exceptions import UpstreamError, UpstreamTimeout

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = settings.HTTP_USER_AGENT
    # Retry only connection setup failures and transient 5xx replies, never read timeouts:
    # a slow upstream should fail fast instead of multiplying the wait.
    retry = Retry(
        total=1,
        connect=1,
        read=0,
        status=1,
        backoff_factor=0.5,
        status_forcelist=(502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


def _is_timeout(exc: requests.RequestException) -> bool:
    """True for timeouts, including those requests re-raises as a ConnectionError.

    When urllib3 exhausts its retries after a timeout, requests wraps the cause in a plain
    ConnectionError, so ``requests.Timeout`` alone misses it.
    """
    if isinstance(exc, requests.Timeout):
        return True
    for arg in exc.args:
        reason = getattr(arg, "reason", arg)
        if isinstance(reason, urllib3.exceptions.TimeoutError):
            return True
    return False


def get_json(service: str, url: str, params: dict | None = None):
    """GET ``url`` and return decoded JSON, mapping every failure to a ServiceError."""
    try:
        response = _session().get(url, params=params, timeout=settings.HTTP_TIMEOUT)
    except requests.RequestException as exc:
        if _is_timeout(exc):
            logger.warning("%s request timed out: %s", service, exc)
            raise UpstreamTimeout(f"The {service} service timed out.") from exc
        logger.warning("%s request failed: %s", service, exc)
        raise UpstreamError(f"The {service} service is unreachable.") from exc

    # OSRM reports routing failures (e.g. NoRoute) as HTTP 400 with a JSON body that the
    # caller needs to interpret, so only treat non-JSON or 5xx/429 replies as outages.
    try:
        payload = response.json()
    except ValueError as exc:
        logger.warning("%s returned non-JSON (HTTP %s)", service, response.status_code)
        raise UpstreamError(f"The {service} service returned an invalid response.") from exc
    if response.status_code >= 500 or response.status_code == 429:
        logger.warning("%s returned HTTP %s", service, response.status_code)
        raise UpstreamError(f"The {service} service returned HTTP {response.status_code}.")
    return payload
