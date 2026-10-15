"""The real HTTP layer (timeouts, retries, bad replies) exercised against a local server.

Unlike the other API tests nothing in `requests` is mocked here: a throwaway HTTP server on
127.0.0.1 plays the part of Nominatim and misbehaves in controlled ways.
"""
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from rest_framework.test import APIClient

from fuelroute.services import http as http_module


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        server = self.server
        server.hits += 1
        if server.behaviour == "slow":
            time.sleep(2)
        status, body = (503, b"{}") if server.behaviour == "503" else (200, server.body)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def log_message(self, *args):
        pass


@pytest.fixture
def upstream(settings):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.hits, server.behaviour, server.body = 0, "ok", b"[]"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    settings.NOMINATIM_BASE_URL = f"http://127.0.0.1:{server.server_port}"
    settings.HTTP_TIMEOUT = (1.0, 0.3)
    http_module._session.cache_clear()
    yield server
    server.shutdown()
    http_module._session.cache_clear()


def _post():
    return APIClient().post("/api/route/", {"start": "Anywhere", "finish": "Elsewhere"}, format="json")


def test_slow_upstream_times_out_quickly_without_retrying(upstream):
    upstream.behaviour = "slow"
    started = time.monotonic()
    response = _post()
    elapsed = time.monotonic() - started
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "upstream_timeout"
    assert elapsed < 1.5, "a read timeout must not be retried"
    assert upstream.hits == 1


def test_transient_5xx_is_retried_once_then_reported_as_502(upstream):
    upstream.behaviour = "503"
    response = _post()
    assert response.status_code == 502
    assert upstream.hits == 2  # original + exactly one retry


def test_non_json_reply_is_502(upstream):
    upstream.body = b"<html>rate limited</html>"
    response = _post()
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_error"


def test_unreachable_upstream_is_a_gateway_error(settings):
    import socket

    with socket.socket() as sock:  # grab a free port, then close it so nothing is listening
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    settings.NOMINATIM_BASE_URL = f"http://127.0.0.1:{port}"
    settings.HTTP_TIMEOUT = (1.0, 1.0)
    http_module._session.cache_clear()
    try:
        response = _post()
        # Linux refuses the connection at once (502); Windows lets it hang until the connect
        # timeout (504). Either way it is a gateway error with a structured body, never a 500.
        assert response.status_code in (502, 504)
        assert response.json()["error"]["code"] in ("upstream_error", "upstream_timeout")
    finally:
        http_module._session.cache_clear()


def test_empty_result_list_is_404_over_real_http(upstream):
    response = _post()
    assert response.status_code == 404
    assert json.loads(response.content)["error"]["code"] == "location_not_found"


# --- environment configuration ---------------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]


def _load_settings(**env):
    base = {"DJANGO_SETTINGS_MODULE": "config.settings", "SYSTEMROOT": "C:\\Windows"}
    code = "from django.conf import settings; settings.SECRET_KEY"
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env={**base, **env}, capture_output=True, text=True)


@pytest.mark.parametrize(
    "env",
    [
        {"DJANGO_DEBUG": "false"},  # no secret key at all
        {"DJANGO_DEBUG": "false", "DJANGO_SECRET_KEY": "change-me"},  # the .env.example placeholder
        {"DJANGO_DEBUG": "false", "DJANGO_SECRET_KEY": "short"},
        {"VEHICLE_MPG": "0"},
        {"INITIAL_FUEL_FRACTION": "1.5"},
        {"STATION_CORRIDOR_MILES": "-1"},
        {"CACHE_BACKEND": "memcached"},
        {"VEHICLE_RANGE_MILES": "abc"},
    ],
)
def test_bad_configuration_fails_fast(env):
    result = _load_settings(**env)
    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr


def test_production_configuration_with_a_real_key_loads():
    result = _load_settings(DJANGO_DEBUG="false", DJANGO_SECRET_KEY="k" * 50)
    assert result.returncode == 0, result.stderr
