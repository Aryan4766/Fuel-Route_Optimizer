"""Shared fixtures: an in-memory fake of Nominatim + OSRM, and a small synthetic station file."""
import csv
import json

import numpy as np
import pytest
import requests
from django.core.cache import cache

from fuelroute.services import http as http_module
from fuelroute.services import stations as stations_module
from fuelroute.services.geometry import haversine_miles

# A straight east-west drive along latitude 40N from lon -100 to -80 (about 1,060 miles).
ROUTE_LAT = 40.0
ROUTE_START = (ROUTE_LAT, -100.0)
ROUTE_END = (ROUTE_LAT, -80.0)

STATION_HEADER = ["opis_id", "name", "address", "city", "state", "price", "lat", "lon", "geocode_source"]


def synthetic_route_payload(start=ROUTE_START, end=ROUTE_END, step_deg=0.05):
    lons = np.arange(start[1], end[1] + 1e-9, step_deg)
    coords = [[float(lon), ROUTE_LAT] for lon in lons]
    miles = float(haversine_miles(start[0], start[1], end[0], end[1]))
    return {
        "code": "Ok",
        "routes": [
            {
                "distance": miles * 1609.344,
                "duration": miles / 60 * 3600,
                "geometry": {"type": "LineString", "coordinates": coords},
            }
        ],
    }


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """Stands in for requests.Session; records every call so tests can count API usage."""

    def __init__(self):
        self.places = {
            "start town": {"display_name": "Start Town, USA", "lat": "40.0", "lon": "-100.0"},
            "end city": {"display_name": "End City, USA", "lat": "40.0", "lon": "-80.0"},
            "london": {"display_name": "London, UK", "lat": "51.5", "lon": "-0.12"},
        }
        self.osrm_payload = synthetic_route_payload()
        self.osrm_status = 200
        self.raise_on = {}  # {"osrm": exception, "nominatim": exception}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        service = "nominatim" if "/search" in url else "osrm"
        self.calls.append((service, url, params, timeout))
        if service in self.raise_on:
            raise self.raise_on[service]
        if service == "nominatim":
            hit = self.places.get(params["q"].lower())
            return FakeResponse([hit] if hit else [])
        return FakeResponse(self.osrm_payload, self.osrm_status)

    def count(self, service):
        return sum(1 for call in self.calls if call[0] == service)


@pytest.fixture
def fake_session(monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(http_module, "_session", lambda: session)
    return session


@pytest.fixture(autouse=True)
def _isolated_state(settings, tmp_path):
    """Each test gets an empty cache, a fresh station index and generous rate limits."""
    cache.clear()
    stations_module.get_station_index.cache_clear()
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {"anon": "10000/min"},
    }
    yield
    cache.clear()
    stations_module.get_station_index.cache_clear()


def write_stations(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(STATION_HEADER)
        writer.writerows(rows)


@pytest.fixture
def station_file(tmp_path, settings):
    """Stations along the synthetic route. Positions in miles are roughly lon-offset * 53."""
    rows = [
        # id, name, address, city, state, price, lat, lon, source
        (1, "CHEAP FUEL", "I-80 EXIT 1", "Alpha", "NE", "3.000", 40.00, -97.0, "test"),
        (2, "MID FUEL", "I-80 EXIT 2", "Bravo", "IA", "3.400", 40.02, -93.0, "test"),
        (3, "PRICEY FUEL", "I-80 EXIT 3", "Charlie", "IA", "4.000", 40.00, -90.0, "test"),
        (4, "BUDGET FUEL", "I-80 EXIT 4", "Delta", "IL", "3.100", 40.05, -86.0, "test"),
        (5, "FAR AWAY", "N/A", "Echo", "TX", "2.000", 30.00, -95.0, "test"),  # nowhere near the route
    ]
    path = tmp_path / "stations.csv"
    write_stations(path, rows)
    settings.STATIONS_DATA_PATH = path
    return path
