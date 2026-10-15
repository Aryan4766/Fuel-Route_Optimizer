import numpy as np
import pytest

from fuelroute.exceptions import StationDataUnavailable
from fuelroute.services.geometry import RoutePath, haversine_miles
from fuelroute.services.stations import StationIndex, get_station_index, read_stations
from fuelroute.tests.conftest import STATION_HEADER, synthetic_route_payload, write_stations


def straight_path(total=None):
    payload = synthetic_route_payload()["routes"][0]
    miles = payload["distance"] / 1609.344
    return RoutePath(payload["geometry"]["coordinates"], total or miles)


def test_haversine_known_distance():
    # Chicago -> New York is about 711 miles great-circle
    assert float(haversine_miles(41.8781, -87.6298, 40.7128, -74.0060)) == pytest.approx(711, abs=3)


def test_haversine_vectorised_and_zero():
    d = haversine_miles(np.array([40.0, 40.0]), np.array([-90.0, -90.0]), 40.0, -90.0)
    assert d.tolist() == [0.0, 0.0]


def test_route_needs_two_points():
    with pytest.raises(ValueError):
        RoutePath([[0, 0]], 1)


def test_match_positions_and_offsets():
    path = straight_path()
    # ~0.1 deg lat north of the route = 6.9 miles off; at lon -90 => about halfway.
    hits = path.match([40.1, 40.0], [-90.0, -85.0], max_offset_miles=10)
    assert [h.index for h in hits] == [0, 1]
    assert hits[0].offset_miles == pytest.approx(6.9, abs=0.2)
    assert hits[0].route_mile == pytest.approx(530, abs=8)
    assert hits[1].offset_miles == pytest.approx(0, abs=0.1)
    assert hits[1].route_mile > hits[0].route_mile


def test_match_excludes_points_outside_corridor():
    path = straight_path()
    hits = path.match([40.2, 41.0, 40.0], [-90.0, -90.0, -110.0], max_offset_miles=10)
    assert hits == []  # 13.8 mi, 69 mi and 10 deg past the western end


def test_match_clamps_to_route_end_and_start():
    path = straight_path()
    (hit,) = path.match([40.0], [-79.95], max_offset_miles=10)  # just beyond the end
    assert hit.route_mile == pytest.approx(path.total_miles, abs=0.5)
    assert hit.offset_miles == pytest.approx(2.6, abs=0.3)


def test_route_miles_are_rescaled_to_reported_distance():
    path = straight_path(total=2000.0)
    (hit,) = path.match([40.0], [-80.0], max_offset_miles=5)
    assert hit.route_mile == pytest.approx(2000.0, abs=1.0)


def test_matching_follows_a_curving_route():
    # An "L": east then north. A station near the corner is positioned at the corner's mileage.
    coords = [[-100 + 0.05 * i, 40.0] for i in range(0, 201)] + [[-90.0, 40.0 + 0.05 * i] for i in range(1, 201)]
    miles = sum(
        float(haversine_miles(coords[i][1], coords[i][0], coords[i + 1][1], coords[i + 1][0]))
        for i in range(len(coords) - 1)
    )
    path = RoutePath(coords, miles)
    (corner,) = path.match([40.0], [-90.0], 5)
    (up,) = path.match([45.0], [-90.0], 5)
    assert corner.route_mile == pytest.approx(530, abs=10)
    assert up.route_mile == pytest.approx(530 + 345, abs=10)


# --- station file ----------------------------------------------------------------------
def test_read_stations_skips_malformed_rows(tmp_path):
    path = tmp_path / "s.csv"
    write_stations(
        path,
        [
            (1, "OK", "a", "c", "TX", "3.1", 31.0, -97.0, "t"),
            ("x", "bad id", "a", "c", "TX", "3.1", 31.0, -97.0, "t"),
            (3, "no price", "a", "c", "TX", "", 31.0, -97.0, "t"),
            (4, "zero price", "a", "c", "TX", "0", 31.0, -97.0, "t"),
            (5, "bad lat", "a", "c", "TX", "3.1", 95.0, -97.0, "t"),
            (6, "nan", "a", "c", "TX", "nan", 31.0, -97.0, "t"),
        ],
    )
    stations = read_stations(path)
    assert [s.name for s in stations] == ["OK"]


def test_read_stations_missing_file(tmp_path):
    with pytest.raises(StationDataUnavailable):
        read_stations(tmp_path / "nope.csv")


def test_read_stations_missing_columns(tmp_path):
    path = tmp_path / "s.csv"
    path.write_text("opis_id,name\n1,x\n")
    with pytest.raises(StationDataUnavailable, match="missing columns"):
        read_stations(path)


def test_read_stations_empty(tmp_path):
    path = tmp_path / "s.csv"
    write_stations(path, [])
    with pytest.raises(StationDataUnavailable, match="no usable stations"):
        read_stations(path)


def test_index_is_loaded_once(station_file):
    assert get_station_index() is get_station_index()


def test_along_route_returns_sorted_candidates_in_corridor(station_file):
    index = get_station_index()
    candidates = index.along_route(straight_path(), corridor_miles=10)
    names = [c.ref.name for c in candidates]
    assert names == ["CHEAP FUEL", "MID FUEL", "PRICEY FUEL", "BUDGET FUEL"]  # FAR AWAY excluded
    assert [c.route_mile for c in candidates] == sorted(c.route_mile for c in candidates)
    assert candidates[1].offset_miles == pytest.approx(1.4, abs=0.2)  # 0.02 deg north
    assert candidates[0].price == 3.0


def test_corridor_width_is_respected(station_file):
    index = get_station_index()
    narrow = index.along_route(straight_path(), corridor_miles=2)
    assert [c.ref.name for c in narrow] == ["CHEAP FUEL", "MID FUEL", "PRICEY FUEL"]


def test_committed_station_file_is_loadable():
    from config import settings as project_settings

    path = project_settings.STATIONS_DATA_PATH
    if not path.exists():
        pytest.skip("run `python manage.py build_station_index` first")
    stations = read_stations(path)
    assert len(stations) > 5000
    assert len({s.opis_id for s in stations}) == len(stations), "one record per station"
    assert all(2.0 < s.price < 8.0 for s in stations)
    assert all(24 < s.lat < 50 and -125 < s.lon < -66 for s in stations)


def test_station_at_route_end_is_not_positioned_beyond_it():
    path = straight_path()
    (hit,) = path.match([40.0], [-80.0], max_offset_miles=5)
    assert hit.route_mile <= path.total_miles


def test_prefilter_never_drops_a_true_match():
    """The fast two-stage match must equal brute-force projection of every point."""
    rng = np.random.default_rng(7)
    # A wiggly 1,000-mile route so that the sparse prefilter vertices are not collinear.
    lons = np.linspace(-100, -82, 2500)
    lats = 40 + 3 * np.sin(np.linspace(0, 9, 2500))
    coords = np.column_stack([lons, lats]).tolist()
    miles = float(haversine_miles(lats[:-1], lons[:-1], lats[1:], lons[1:]).sum())
    path = RoutePath(coords, miles)

    pts_lat = rng.uniform(34, 46, 4000)
    pts_lon = rng.uniform(-102, -80, 4000)
    along, offset = path._project(pts_lat, pts_lon)
    expected = set(np.flatnonzero(offset <= 15).tolist())
    got = {m.index for m in path.match(pts_lat, pts_lon, 15)}
    assert got == expected
    assert len(expected) > 100  # the comparison is meaningful
