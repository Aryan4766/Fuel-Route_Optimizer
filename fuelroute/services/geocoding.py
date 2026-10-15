"""Location text -> coordinates (Nominatim), with caching. At most one HTTP call per new place."""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache

from ..exceptions import LocationNotFound, LocationOutOfArea, UpstreamError
from .http import get_json

logger = logging.getLogger(__name__)

# Contiguous United States. Routing and the station data only cover this area.
US_BOUNDS = {"min_lat": 24.3, "max_lat": 49.5, "min_lon": -125.1, "max_lon": -66.8}
_COORD_RE = re.compile(r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$")
_NOT_FOUND_TTL = 3600  # remember misses briefly so repeated bad input costs no API calls


@dataclass(frozen=True, slots=True)
class Location:
    query: str
    label: str
    lat: float
    lon: float
    from_cache: bool = False
    geocoded: bool = False  # True when this call hit the geocoding API


def within_us(lat: float, lon: float) -> bool:
    return (
        US_BOUNDS["min_lat"] <= lat <= US_BOUNDS["max_lat"]
        and US_BOUNDS["min_lon"] <= lon <= US_BOUNDS["max_lon"]
    )


def _cache_key(text: str) -> str:
    normalised = " ".join(text.lower().split())
    return "geocode:" + hashlib.sha256(normalised.encode()).hexdigest()


def resolve_location(text: str) -> Location:
    """Turn "Chicago, IL" or "41.88,-87.63" into a Location inside the supported area."""
    text = text.strip()

    match = _COORD_RE.match(text)
    if match:  # explicit "lat,lon": no API call needed
        lat, lon = float(match.group(1)), float(match.group(2))
        return _checked(Location(text, f"{lat:.5f}, {lon:.5f}", lat, lon))

    key = _cache_key(text)
    cached = cache.get(key)
    if cached is not None:
        if cached == "miss":
            raise LocationNotFound(f"Could not find a location matching '{text}'.")
        return _checked(Location(text, cached["label"], cached["lat"], cached["lon"], from_cache=True))

    results = get_json(
        "geocoding",
        f"{settings.NOMINATIM_BASE_URL}/search",
        {"q": text, "format": "jsonv2", "limit": 1, "countrycodes": "us"},
    )
    if not isinstance(results, list) or not results:
        cache.set(key, "miss", _NOT_FOUND_TTL)
        raise LocationNotFound(f"Could not find a location matching '{text}'.")

    try:
        top = results[0]
        place = {"label": str(top["display_name"]), "lat": float(top["lat"]), "lon": float(top["lon"])}
    except (KeyError, TypeError, ValueError) as exc:
        raise UpstreamError("The geocoding service returned an unexpected response.") from exc

    cache.set(key, place, settings.GEOCODE_CACHE_TTL)
    return _checked(Location(text, place["label"], place["lat"], place["lon"], geocoded=True))


def _checked(location: Location) -> Location:
    if not within_us(location.lat, location.lon):
        raise LocationOutOfArea(
            f"'{location.query}' is outside the supported area (contiguous United States).",
            {"lat": location.lat, "lon": location.lon},
        )
    return location
