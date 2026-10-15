"""Station data: loaded once per process into numpy arrays, then matched against routes."""
from __future__ import annotations

import csv
import logging
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from django.conf import settings

from ..exceptions import StationDataUnavailable
from .geometry import RoutePath
from .optimizer import Candidate

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = {"opis_id", "name", "address", "city", "state", "price", "lat", "lon"}


@dataclass(frozen=True, slots=True)
class Station:
    opis_id: int
    name: str
    address: str
    city: str
    state: str
    price: float  # dollars per gallon
    lat: float
    lon: float
    geocode_source: str = ""  # how the coordinates were derived (see build_station_index)


class StationIndex:
    """All known stations plus coordinate arrays for vectorised route matching."""

    def __init__(self, stations: list[Station]):
        self.stations = stations
        self.lats = np.array([s.lat for s in stations], dtype=float)
        self.lons = np.array([s.lon for s in stations], dtype=float)

    def __len__(self) -> int:
        return len(self.stations)

    def along_route(self, path: RoutePath, corridor_miles: float) -> list[Candidate]:
        """Stations within ``corridor_miles`` of the route, as optimiser candidates."""
        matches = path.match(self.lats, self.lons, corridor_miles)
        return [
            Candidate(m.route_mile, m.offset_miles, self.stations[m.index].price, self.stations[m.index])
            for m in matches
        ]


def read_stations(path: Path) -> list[Station]:
    """Parse the geocoded CSV, skipping (and counting) any malformed row."""
    try:
        handle = open(path, newline="", encoding="utf-8")
    except OSError as exc:
        raise StationDataUnavailable(
            f"Station data file not found at '{path}'. Run `python manage.py build_station_index`."
        ) from exc

    stations: list[Station] = []
    skipped = 0
    with handle:
        reader = csv.DictReader(handle)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise StationDataUnavailable(f"Station data file is missing columns: {sorted(missing)}")
        for row in reader:
            try:
                station = Station(
                    opis_id=int(row["opis_id"]),
                    name=row["name"].strip(),
                    address=row["address"].strip(),
                    city=row["city"].strip(),
                    state=row["state"].strip(),
                    price=float(row["price"]),
                    lat=float(row["lat"]),
                    lon=float(row["lon"]),
                    geocode_source=(row.get("geocode_source") or "").strip(),
                )
            except (ValueError, KeyError, AttributeError):
                skipped += 1
                continue
            if not (math.isfinite(station.price) and station.price > 0 and -90 <= station.lat <= 90 and -180 <= station.lon <= 180):
                skipped += 1
                continue
            stations.append(station)
    if skipped:
        logger.warning("Skipped %d malformed station rows in %s", skipped, path)
    if not stations:
        raise StationDataUnavailable(f"Station data file '{path}' contains no usable stations.")
    return stations


@lru_cache(maxsize=1)
def get_station_index() -> StationIndex:
    """Load the station file once and keep it for the life of the process."""
    stations = read_stations(settings.STATIONS_DATA_PATH)
    logger.info("Loaded %d stations from %s", len(stations), settings.STATIONS_DATA_PATH)
    return StationIndex(stations)
