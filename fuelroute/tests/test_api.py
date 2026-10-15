"""End-to-end API tests with Nominatim and OSRM replaced by an in-memory fake."""
import json

import pytest
import requests
from rest_framework.test import APIClient

from fuelroute.tests.conftest import synthetic_route_payload, write_stations

URL = "/api/route/"
GOOD = {"start": "Start Town", "finish": "End City"}


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def ready(station_file, fake_session):
    return fake_session


def post(client, body=GOOD):
    return client.post(URL, body, format="json")


# --- happy path -------------------------------------------------------------------------
def test_successful_plan_shape_and_math(client, ready):
    response = post(client)
    assert response.status_code == 200
    body = response.json()

    # Route geometry is a valid GeoJSON LineString in [lon, lat] order.
    geometry = body["route"]["geometry"]
    assert geometry["type"] == "LineString"
    assert len(geometry["coordinates"]) > 100
    lon, lat = geometry["coordinates"][0]
    assert -101 < lon < -99 and 39 < lat < 41
    assert body["route"]["distance_miles"] == pytest.approx(1062, abs=10)
    assert body["route"]["duration_seconds"] > 0

    fuel = body["fuel"]
    assert fuel["vehicle"] == {"range_miles": 500.0, "mpg": 10.0, "tank_capacity_gallons": 50.0}
    stops = body["fuel_stops"]
    assert stops, "a 1,060-mile trip must need fuel stops"

    # Costs add up exactly, gallons add up, and consumed = initial + purchased - ending.
    assert fuel["total_fuel_cost"] == pytest.approx(sum(s["cost"] for s in stops), abs=1e-9)
    assert fuel["fuel_purchased_gallons"] == pytest.approx(sum(s["gallons_purchased"] for s in stops), abs=1e-6)
    assert fuel["fuel_consumed_gallons"] == pytest.approx(
        fuel["initial_fuel_gallons"] + fuel["fuel_purchased_gallons"] - fuel["ending_fuel_gallons"], abs=0.01
    )
    for s in stops:
        assert s["cost"] == pytest.approx(s["gallons_purchased"] * s["price_per_gallon"], abs=0.01)
        assert s["fuel_after_purchase_gallons"] <= 50.0 + 1e-6
        assert s["miles_off_route"] <= 10
    # The FAR AWAY station is never chosen
    assert "FAR AWAY" not in {s["station"]["name"] for s in stops}
    # Output is valid JSON all the way down
    json.loads(response.content)


def test_every_stop_is_reachable_from_the_previous_one(client, ready):
    body = post(client).json()
    miles = [0.0] + [s["miles_from_start"] for s in body["fuel_stops"]] + [body["route"]["distance_miles"]]
    for a, b in zip(miles, miles[1:]):
        assert b - a <= 500 + 1e-6


def test_external_calls_are_minimal_and_cached(client, ready):
    first = post(client).json()
    assert ready.count("nominatim") == 2
    assert ready.count("osrm") == 1
    assert first["meta"]["external_api_calls"] == {"geocoding": 2, "routing": 1}

    second = post(client).json()  # identical request: everything from cache
    assert ready.count("nominatim") == 2
    assert ready.count("osrm") == 1
    assert second["meta"]["external_api_calls"] == {"geocoding": 0, "routing": 0}
    assert second["fuel"] == first["fuel"]
    assert second["fuel_stops"] == first["fuel_stops"]


def test_geocode_cache_is_case_and_whitespace_insensitive(client, ready):
    post(client)
    post(client, {"start": "  START   town ", "finish": "end CITY"})
    assert ready.count("nominatim") == 2


def test_requests_use_configured_timeout(client, ready, settings):
    post(client)
    assert all(call[3] == settings.HTTP_TIMEOUT for call in ready.calls)


def test_coordinates_skip_geocoding(client, ready):
    response = post(client, {"start": "40.0,-100.0", "finish": "40.0, -80.0"})
    assert response.status_code == 200
    assert ready.count("nominatim") == 0
    assert ready.count("osrm") == 1


def test_short_trip_needs_no_stops_and_costs_nothing(client, station_file, fake_session):
    fake_session.osrm_payload = synthetic_route_payload(end=(40.0, -95.0))  # ~265 miles
    body = post(client).json()
    assert body["fuel_stops"] == []
    assert body["fuel"]["total_fuel_cost"] == 0
    assert body["fuel"]["fuel_purchased_gallons"] == 0
    assert body["fuel"]["ending_fuel_gallons"] == pytest.approx(50 - body["route"]["distance_miles"] / 10, abs=0.01)


def test_initial_fuel_fraction_setting_is_respected(client, ready, settings):
    settings.INITIAL_FUEL_FRACTION = 0.5
    body = post(client).json()
    assert body["fuel"]["initial_fuel_gallons"] == 25.0


# --- validation -------------------------------------------------------------------------
@pytest.mark.parametrize(
    "payload, field",
    [
        ({}, "start"),
        ({"start": "A"}, "finish"),
        ({"finish": "B"}, "start"),
        ({"start": "", "finish": "B"}, "start"),
        ({"start": "   ", "finish": "B"}, "start"),
        ({"start": "A", "finish": "x" * 201}, "finish"),
        ({"start": None, "finish": "B"}, "start"),
        ({"start": 12, "finish": "B"}, None),  # coerced to a string; not a validation error on its own
    ],
)
def test_validation_errors(client, ready, payload, field):
    response = post(client, payload)
    if field is None:
        assert response.status_code != 500
        return
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert field in error["details"]
    assert ready.calls == [], "invalid input must not trigger any external call"


def test_same_start_and_finish_rejected(client, ready):
    response = post(client, {"start": "Dallas, TX", "finish": " dallas,  tx"})
    assert response.status_code == 400
    assert ready.calls == []


def test_malformed_json_body(client, ready):
    response = client.post(URL, data="{not json", content_type="application/json")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "parse_error"


def test_unsupported_content_type(client, ready):
    response = client.post(URL, data="start=a", content_type="application/x-www-form-urlencoded")
    assert response.status_code == 415


def test_method_not_allowed(client, ready):
    response = client.get(URL)
    assert response.status_code == 405
    assert "error" in response.json()


# --- upstream / domain failures ---------------------------------------------------------
def test_unknown_location_is_404(client, ready):
    response = post(client, {"start": "Nowhereville", "finish": "End City"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "location_not_found"
    assert ready.count("osrm") == 0


def test_failed_lookup_is_cached(client, ready):
    post(client, {"start": "Nowhereville", "finish": "End City"})
    post(client, {"start": "Nowhereville", "finish": "End City"})
    assert ready.count("nominatim") == 1


def test_location_outside_us_is_422(client, ready):
    response = post(client, {"start": "London", "finish": "End City"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "location_out_of_area"


def test_no_route_is_422(client, ready):
    ready.osrm_payload = {"code": "NoRoute", "message": "Impossible route"}
    ready.osrm_status = 400
    response = post(client)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "no_route"


def test_routing_timeout_is_504(client, ready):
    ready.raise_on["osrm"] = requests.Timeout("slow")
    response = post(client)
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "upstream_timeout"


def test_geocoder_timeout_is_504(client, ready):
    ready.raise_on["nominatim"] = requests.ConnectTimeout("slow")
    assert post(client).status_code == 504


def test_routing_connection_error_is_502(client, ready):
    ready.raise_on["osrm"] = requests.ConnectionError("refused")
    response = post(client)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_error"


def test_routing_server_error_is_502(client, ready):
    ready.osrm_status = 503
    ready.osrm_payload = {"message": "down"}
    assert post(client).status_code == 502


def test_garbage_routing_reply_is_502(client, ready):
    ready.osrm_payload = {"code": "Ok", "routes": [{"nope": 1}]}
    assert post(client).status_code == 502


def test_upstream_failure_is_not_cached(client, ready):
    ready.raise_on["osrm"] = requests.Timeout("slow")
    post(client)
    del ready.raise_on["osrm"]
    assert post(client).status_code == 200


def test_no_stations_to_cover_long_gap_is_422(client, tmp_path, settings, fake_session):
    write_stations(tmp_path / "far.csv", [(9, "FAR", "x", "Echo", "TX", "3.0", 30.0, -95.0, "test")])
    settings.STATIONS_DATA_PATH = tmp_path / "far.csv"
    response = post(client)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "no_feasible_fuel_plan"
    assert "No fuel station" in error["message"]


def test_missing_station_file_is_503(client, tmp_path, settings, fake_session):
    settings.STATIONS_DATA_PATH = tmp_path / "does-not-exist.csv"
    response = post(client)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "station_data_unavailable"


# --- misc -------------------------------------------------------------------------------
def test_health_endpoint(client, station_file):
    response = client.get("/api/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "stations_loaded": 5}


def test_throttling_returns_429(client, ready, settings):
    from rest_framework.throttling import AnonRateThrottle

    AnonRateThrottle.THROTTLE_RATES = {"anon": "2/min"}
    try:
        statuses = [post(client).status_code for _ in range(3)]
    finally:
        AnonRateThrottle.THROTTLE_RATES = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    assert statuses[-1] == 429
