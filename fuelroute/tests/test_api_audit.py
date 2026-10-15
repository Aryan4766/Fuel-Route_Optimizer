"""Response accounting and honesty checks."""
import pytest
from rest_framework.test import APIClient

URL = "/api/route/"
GOOD = {"start": "Start Town", "finish": "End City"}


@pytest.fixture
def body(station_file, fake_session):
    return APIClient().post(URL, GOOD, format="json").json()


def test_fuel_accounting_identities(body):
    fuel, route = body["fuel"], body["route"]
    # consumed = (route distance + detour miles) / mpg   (MPG = 10)
    assert fuel["fuel_consumed_gallons"] == pytest.approx((route["distance_miles"] + fuel["detour_miles"]) / 10, abs=0.01)
    assert fuel["detour_miles"] == pytest.approx(sum(s["detour_miles"] for s in body["fuel_stops"]), abs=0.1)
    # consumed = initial + purchased - ending
    assert fuel["fuel_consumed_gallons"] == pytest.approx(
        fuel["initial_fuel_gallons"] + fuel["fuel_purchased_gallons"] - fuel["ending_fuel_gallons"], abs=0.01
    )
    # purchased is not consumed: the starting tank was free
    assert fuel["fuel_purchased_gallons"] < fuel["fuel_consumed_gallons"]
    assert fuel["ending_fuel_gallons"] >= 0
    for stop in body["fuel_stops"]:
        assert stop["fuel_after_purchase_gallons"] == pytest.approx(
            stop["fuel_on_arrival_gallons"] + stop["gallons_purchased"], abs=0.002
        )


def test_station_locations_are_flagged_as_city_level(body):
    assert body["fuel_stops"]
    for stop in body["fuel_stops"]:
        assert stop["station"]["location"]["precision"] == "city_centroid"
        assert stop["station"]["location"]["source"] == "test"


def test_average_price_matches_cost_over_gallons(body):
    fuel = body["fuel"]
    assert fuel["average_price_per_gallon"] == pytest.approx(
        fuel["total_fuel_cost"] / fuel["fuel_purchased_gallons"], abs=0.001
    )
