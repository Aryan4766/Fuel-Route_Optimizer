"""Build data/stations_geocoded.csv from the raw OPIS price file.

Usage:
    python manage.py build_station_index [--source FILE] [--output FILE] [--skip-nominatim]

Coordinates come from the US Census Gazetteer (city centres). Cities it cannot match are
looked up in Nominatim, at most one request per second, and remembered in a JSON cache so
re-running the command makes no repeat calls. Stations that still cannot be placed are
dropped and listed, never guessed.
"""
import csv
import io
import json
import time
import zipfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from fuelroute.services.geocoding import within_us
from fuelroute.services.http import get_json
from fuelroute.services.station_prep import STATE_NAMES, Gazetteer, load_unique_us_stations
import requests

GAZETTEER_URL = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_Gaz_place_national.zip"
NOMINATIM_DELAY_SECONDS = 1.1  # Nominatim usage policy: max 1 request/second
OUTPUT_COLUMNS = ["opis_id", "name", "address", "city", "state", "price", "lat", "lon", "geocode_source"]


class Command(BaseCommand):
    help = "Deduplicate the OPIS fuel price CSV and attach coordinates to every station."

    def add_arguments(self, parser):
        data = Path(settings.BASE_DIR) / "data"
        parser.add_argument("--source", default=str(Path(settings.BASE_DIR) / "fuel-prices-for-be-assessment.csv"))
        parser.add_argument("--output", default=str(settings.STATIONS_DATA_PATH))
        parser.add_argument("--gazetteer", help="Local Census Gazetteer .zip or .txt (default: download it)")
        parser.add_argument("--nominatim-cache", default=str(data / "nominatim_city_cache.json"))
        parser.add_argument("--skip-nominatim", action="store_true", help="Only use the Census Gazetteer")

    def handle(self, *args, **opts):
        try:
            raw_text = Path(opts["source"]).read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise CommandError(f"Cannot read source CSV: {exc}") from exc

        stations, report = load_unique_us_stations(raw_text)
        self.stdout.write(
            f"{report.rows} rows -> {report.stations} unique US stations "
            f"({report.non_us_rows} non-US rows dropped, {report.invalid_rows} invalid rows dropped)"
        )

        gazetteer = Gazetteer.from_text(self._gazetteer_text(opts["gazetteer"]))
        cache_path = Path(opts["nominatim_cache"])
        nominatim_cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}

        resolved: dict[tuple[str, str], tuple[float, float, str] | None] = {}
        for city, state in sorted({(s.city, s.state) for s in stations}):
            found = gazetteer.lookup(city, state)
            if found:
                place, how = found
                resolved[(city, state)] = (place.lat, place.lon, f"census_gazetteer_{how}")
            else:
                resolved[(city, state)] = None

        missing = [key for key, value in resolved.items() if value is None]
        self.stdout.write(f"Gazetteer matched {len(resolved) - len(missing)}/{len(resolved)} cities; {len(missing)} left")

        if missing and not opts["skip_nominatim"]:
            self._nominatim_pass(missing, resolved, nominatim_cache, cache_path)

        written, dropped = 0, []
        output = Path(opts["output"])
        output.parent.mkdir(parents=True, exist_ok=True)
        with open(output, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(OUTPUT_COLUMNS)
            for s in stations:
                hit = resolved[(s.city, s.state)]
                if hit is None:
                    dropped.append(s)
                    continue
                lat, lon, source = hit
                writer.writerow([s.opis_id, s.name, s.address, s.city, s.state, f"{s.price:.5f}", f"{lat:.6f}", f"{lon:.6f}", source])
                written += 1

        self.stdout.write(self.style.SUCCESS(f"Wrote {written} stations to {output}"))
        if dropped:
            self.stdout.write(self.style.WARNING(f"Dropped {len(dropped)} stations with no resolvable city:"))
            for s in dropped:
                self.stdout.write(f"  #{s.opis_id} {s.name} - {s.city}, {s.state}")

    def _gazetteer_text(self, local: str | None) -> str:
        if local:
            path = Path(local)
            blob = path.read_bytes()
        else:
            self.stdout.write(f"Downloading {GAZETTEER_URL}")
            try:
                response = requests.get(GAZETTEER_URL, timeout=settings.HTTP_TIMEOUT[1] * 3)
                response.raise_for_status()
            except requests.RequestException as exc:
                raise CommandError(f"Could not download the Census Gazetteer: {exc}") from exc
            blob = response.content
        if blob[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                name = next(n for n in archive.namelist() if n.endswith(".txt"))
                blob = archive.read(name)
        return blob.decode("utf-8-sig")

    def _nominatim_pass(self, missing, resolved, cache_data, cache_path):
        self.stdout.write(f"Looking up {len(missing)} cities in Nominatim (about {len(missing) * 1.1 / 60:.0f} min)")
        for n, (city, state) in enumerate(missing, 1):
            key = f"{city}|{state}"
            if key not in cache_data:
                cache_data[key] = self._nominatim_lookup(city, state)
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(cache_data, indent=0, sort_keys=True))
                time.sleep(NOMINATIM_DELAY_SECONDS)
            coords = cache_data[key]
            if coords:
                resolved[(city, state)] = (coords[0], coords[1], "nominatim")
            if n % 50 == 0:
                self.stdout.write(f"  {n}/{len(missing)}")

    @staticmethod
    def _nominatim_lookup(city, state):
        results = get_json(
            "geocoding",
            f"{settings.NOMINATIM_BASE_URL}/search",
            {"city": city, "state": STATE_NAMES[state], "country": "us", "format": "jsonv2", "limit": 1},
        )
        if not results:
            results = get_json(
                "geocoding",
                f"{settings.NOMINATIM_BASE_URL}/search",
                {"q": f"{city}, {STATE_NAMES[state]}", "countrycodes": "us", "format": "jsonv2", "limit": 1},
            )
            time.sleep(NOMINATIM_DELAY_SECONDS)
        if not results:
            return None
        lat, lon = float(results[0]["lat"]), float(results[0]["lon"])
        return [lat, lon] if within_us(lat, lon) else None
