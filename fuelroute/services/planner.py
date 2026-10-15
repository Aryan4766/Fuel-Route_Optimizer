"""Orchestrates one request: geocode -> route -> stations along route -> optimise."""
from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings

from ..exceptions import NoRouteFound
from .geocoding import resolve_location
from .geometry import RoutePath
from .optimizer import optimize_fuel_stops
from .routing import get_route
from .stations import get_station_index

logger = logging.getLogger(__name__)

_CENT = Decimal("0.01")
_MIN_ROUTE_MILES = 0.1


def _money(value: float | Decimal) -> Decimal:
    return Decimal(value).quantize(_CENT, rounding=ROUND_HALF_UP)


def plan_trip(start_text: str, finish_text: str) -> dict:
    """Return the JSON-ready response body for a start/finish pair."""
    range_miles = settings.VEHICLE_RANGE_MILES
    mpg = settings.VEHICLE_MPG
    capacity = range_miles / mpg
    initial_fuel = capacity * settings.INITIAL_FUEL_FRACTION

    start = resolve_location(start_text)
    finish = resolve_location(finish_text)
    route = get_route(start, finish)
    if route.distance_miles < _MIN_ROUTE_MILES:
        raise NoRouteFound("Start and finish resolve to the same place; there is nothing to route.")

    path = RoutePath(route.coordinates, route.distance_miles)
    index = get_station_index()
    candidates = index.along_route(path, settings.STATION_CORRIDOR_MILES)
    plan = optimize_fuel_stops(
        route.distance_miles,
        candidates,
        range_miles=range_miles,
        mpg=mpg,
        initial_fuel_gallons=initial_fuel,
    )

    stops, total_cost, total_gallons = [], Decimal("0"), Decimal("0")
    for n, stop in enumerate(plan.stops, 1):
        station = stop.candidate.ref
        gallons = Decimal(stop.gallons_purchased).quantize(Decimal("0.001"))
        price = Decimal(station.price).quantize(Decimal("0.00001"))
        cost = _money(gallons * price)  # cost of the gallons as reported, so the output adds up
        total_cost += cost
        total_gallons += gallons
        stops.append(
            {
                "stop": n,
                "station": {
                    "opis_id": station.opis_id,
                    "name": station.name,
                    "address": station.address,
                    "city": station.city,
                    "state": station.state,
                    # Coordinates are the centre of the station's city, not the station itself.
                    "location": {
                        "lat": round(station.lat, 6),
                        "lon": round(station.lon, 6),
                        "precision": "city_centroid",
                        "source": station.geocode_source,
                    },
                },
                "miles_from_start": round(stop.candidate.route_mile, 1),
                "miles_off_route": round(stop.candidate.offset_miles, 1),
                "detour_miles": round(2 * stop.candidate.offset_miles, 1),
                "price_per_gallon": float(round(price, 3)),
                "gallons_purchased": float(gallons),
                "cost": float(cost),
                "fuel_on_arrival_gallons": round(stop.fuel_on_arrival, 3),
                "fuel_after_purchase_gallons": round(stop.fuel_after_purchase, 3),
            }
        )

    average = float(round(total_cost / total_gallons, 3)) if total_gallons else None
    response = {
        "start": _location_json(start),
        "finish": _location_json(finish),
        "route": {
            "distance_miles": round(route.distance_miles, 2),
            "duration_seconds": round(route.duration_seconds),
            "duration_hours": round(route.duration_seconds / 3600, 2),
            "geometry": {"type": "LineString", "coordinates": route.coordinates},
        },
        "fuel": {
            "vehicle": {"range_miles": range_miles, "mpg": mpg, "tank_capacity_gallons": capacity},
            "initial_fuel_gallons": round(plan.initial_fuel_gallons, 3),
            "fuel_consumed_gallons": round(plan.fuel_consumed_gallons, 3),
            "detour_miles": round(sum(2 * s.candidate.offset_miles for s in plan.stops), 1),
            "fuel_purchased_gallons": float(total_gallons),
            "ending_fuel_gallons": round(plan.ending_fuel_gallons, 3),
            "average_price_per_gallon": average,
            "total_fuel_cost": float(total_cost),
            "currency": "USD",
        },
        "fuel_stops": stops,
        "meta": {
            "stations_in_corridor": len(candidates),
            "corridor_miles": settings.STATION_CORRIDOR_MILES,
            "external_api_calls": {
                "geocoding": int(start.geocoded) + int(finish.geocoded),
                "routing": 0 if route.from_cache else 1,
            },
            "route_from_cache": route.from_cache,
        },
    }
    logger.info(
        "Planned %.0f mi route with %d stops, $%s (%d candidates)",
        route.distance_miles, len(stops), total_cost, len(candidates),
    )
    return response


def _location_json(location) -> dict:
    return {
        "query": location.query,
        "label": location.label,
        "lat": round(location.lat, 6),
        "lon": round(location.lon, 6),
    }
