"""Driving route between two coordinates (OSRM), cached. One HTTP call per uncached route."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache

from ..exceptions import NoRouteFound, UpstreamError
from .geocoding import Location
from .http import get_json

logger = logging.getLogger(__name__)

METERS_PER_MILE = 1609.344


@dataclass(frozen=True, slots=True)
class Route:
    distance_miles: float
    duration_seconds: float
    coordinates: list  # GeoJSON order: [[lon, lat], ...]
    from_cache: bool = False


def _cache_key(origin: Location, destination: Location) -> str:
    return "route:{:.4f},{:.4f};{:.4f},{:.4f}".format(origin.lat, origin.lon, destination.lat, destination.lon)


def get_route(origin: Location, destination: Location) -> Route:
    key = _cache_key(origin, destination)
    cached = cache.get(key)
    if cached is not None:
        return Route(cached["distance_miles"], cached["duration_seconds"], cached["coordinates"], from_cache=True)

    url = (
        f"{settings.OSRM_BASE_URL}/route/v1/driving/"
        f"{origin.lon:.6f},{origin.lat:.6f};{destination.lon:.6f},{destination.lat:.6f}"
    )
    payload = get_json(
        "routing",
        url,
        {"overview": "full", "geometries": "geojson", "steps": "false", "alternatives": "false"},
    )

    code = payload.get("code") if isinstance(payload, dict) else None
    if code in {"NoRoute", "NoSegment"}:
        raise NoRouteFound("No driving route exists between the two locations.")
    if code != "Ok" or not payload.get("routes"):
        logger.error("Unexpected OSRM reply: %s", str(payload)[:300])
        raise UpstreamError("The routing service returned an unexpected response.")

    try:
        best = payload["routes"][0]
        route = {
            "distance_miles": float(best["distance"]) / METERS_PER_MILE,
            "duration_seconds": float(best["duration"]),
            "coordinates": best["geometry"]["coordinates"],
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise UpstreamError("The routing service returned an unexpected response.") from exc
    if len(route["coordinates"]) < 2:
        raise NoRouteFound("The two locations are too close together to produce a route.")

    cache.set(key, route, settings.ROUTE_CACHE_TTL)
    return Route(**route)
