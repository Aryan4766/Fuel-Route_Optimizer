"""Offline preparation of the station file: dedupe the OPIS CSV and attach coordinates.

The source CSV has addresses but no coordinates. Rather than invent any, each station is
placed at the centre of its city, taken from the US Census Bureau Gazetteer (public domain).
Cities the Gazetteer cannot match are looked up once in Nominatim by the build command.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

CANADIAN_PROVINCES = {"AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"}

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}

_PLACE_SUFFIX = re.compile(
    r"\s+(city and borough|consolidated government|metropolitan government|unified government|"
    r"urban county|county|city|town|village|borough|municipality|cdp|township|plantation)$"
)
_ABBREVIATIONS = (("saint", "st"), ("sainte", "ste"), ("mount", "mt"), ("fort", "ft"))


def normalise_place(name: str) -> str:
    """Lower-case, strip punctuation and standardise St./Mt./Ft. so spellings compare equal."""
    text = name.lower().replace("&", " and ")
    text = re.sub(r"\([^)]*\)", " ", text)  # "(balance)"
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    for long, short in _ABBREVIATIONS:
        text = re.sub(rf"\b{long}\b", short, text)
    return re.sub(r"\s+", " ", text).strip()


def strip_place_suffix(normalised: str) -> str:
    """'abbeville city' -> 'abbeville' (applied repeatedly for 'x city and borough')."""
    previous = None
    while previous != normalised:
        previous = normalised
        normalised = _PLACE_SUFFIX.sub("", normalised)
    return normalised


def _compact(normalised: str) -> str:
    """Ignore spaces so 'Mc Calla' == 'McCalla' and "O'Neill" == 'Oneill'."""
    return normalised.replace(" ", "")


@dataclass(frozen=True, slots=True)
class Place:
    lat: float
    lon: float
    land_sqmi: float
    incorporated: bool


class Gazetteer:
    """(state, city) -> coordinates, built from the Census place Gazetteer."""

    def __init__(self):
        self._exact: dict[tuple[str, str], Place] = {}
        self._by_state: dict[str, list[tuple[str, Place]]] = {}

    @classmethod
    def from_text(cls, text: str) -> "Gazetteer":
        gaz = cls()
        reader = csv.reader(io.StringIO(text), delimiter="\t")
        header = [h.strip() for h in next(reader)]
        col = {name: header.index(name) for name in ("USPS", "NAME", "LSAD", "ALAND_SQMI", "INTPTLAT", "INTPTLONG")}
        for row in reader:
            if len(row) < len(header):
                continue
            state, raw_name = row[col["USPS"]].strip(), row[col["NAME"]]
            try:
                place = Place(
                    lat=float(row[col["INTPTLAT"]]),
                    lon=float(row[col["INTPTLONG"]]),
                    land_sqmi=float(row[col["ALAND_SQMI"]]),
                    incorporated=row[col["LSAD"]].strip() != "57",  # 57 = census-designated place
                )
            except ValueError:
                continue
            full = normalise_place(raw_name)
            gaz._add((state, _compact(strip_place_suffix(full))), place)
            gaz._by_state.setdefault(state, []).append((full, place))
        return gaz

    def _add(self, key: tuple[str, str], place: Place) -> None:
        current = self._exact.get(key)
        # Prefer incorporated places, then the larger one, when names collide in a state.
        if current is None or (place.incorporated, place.land_sqmi) > (current.incorporated, current.land_sqmi):
            self._exact[key] = place

    def lookup(self, city: str, state: str) -> tuple[Place, str] | None:
        """Return (place, how) where ``how`` is 'exact' or 'prefix'; None when unmatched."""
        key = strip_place_suffix(normalise_place(city))
        if not key:
            return None
        place = self._exact.get((state, _compact(key)))
        if place:
            return place, "exact"
        # e.g. "Nashville" -> "Nashville-Davidson metropolitan government (balance)"
        starts = [p for full, p in self._by_state.get(state, []) if full.startswith(key + " ")]
        if starts:
            return max(starts, key=lambda p: (p.incorporated, p.land_sqmi)), "prefix"
        return None


@dataclass(frozen=True, slots=True)
class RawStation:
    opis_id: int
    name: str
    address: str
    city: str
    state: str
    price: float


@dataclass(slots=True)
class LoadReport:
    rows: int = 0
    invalid_rows: int = 0
    non_us_rows: int = 0
    stations: int = 0


def load_unique_us_stations(csv_text: str) -> tuple[list[RawStation], LoadReport]:
    """One record per OPIS truckstop ID, using the lowest listed price for that stop.

    The file repeats a stop on several rows (different price observations and name
    spellings) and also lists Canadian stops, which are outside the routing area.
    """
    report = LoadReport()
    best: dict[int, RawStation] = {}
    for row in csv.DictReader(io.StringIO(csv_text)):
        report.rows += 1
        try:
            station = RawStation(
                opis_id=int(row["OPIS Truckstop ID"]),
                name=" ".join(row["Truckstop Name"].split()),
                address=" ".join(row["Address"].split()),
                city=" ".join(row["City"].split()),
                state=row["State"].strip().upper(),
                price=float(row["Retail Price"]),
            )
        except (KeyError, ValueError, AttributeError):
            report.invalid_rows += 1
            continue
        if not station.city or not station.state or station.price <= 0:
            report.invalid_rows += 1
            continue
        if station.state in CANADIAN_PROVINCES or station.state not in STATE_NAMES:
            report.non_us_rows += 1
            continue
        current = best.get(station.opis_id)
        if current is None or station.price < current.price:
            best[station.opis_id] = station
    report.stations = len(best)
    return sorted(best.values(), key=lambda s: s.opis_id), report
