"""Geospatial helpers: distances along a route and corridor matching for stations.

Everything is vectorised with numpy so that thousands of stations can be matched against
thousands of route segments in well under a second without a spatial database.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

EARTH_RADIUS_MILES = 3958.7613
MILES_PER_DEGREE_LAT = 69.093  # mean length of one degree of latitude

# Route vertices closer together than this are dropped before station matching. A 1-mile
# chord deviates from the true curve by far less than the corridor width, so matching
# accuracy is unaffected while the work shrinks by ~50x.
DEFAULT_SPACING_MILES = 1.0
_CHUNK = 256  # stations processed per vectorised block (bounds memory use)
_COARSE_SPACING_MILES = 25.0  # spacing of the sparse vertex subset used for the first-pass filter


def haversine_miles(lat1, lon1, lat2, lon2):
    """Great-circle distance in miles. Accepts scalars or numpy arrays."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(a))


@dataclass(frozen=True, slots=True)
class RouteMatch:
    """Where a point sits relative to the route."""

    index: int  # index into the arrays that were passed to `RoutePath.match`
    route_mile: float  # distance travelled along the route when closest to the point
    offset_miles: float  # straight-line distance from the route


class RoutePath:
    """A driving route as a polyline, with cumulative distance along it.

    ``coordinates`` are GeoJSON-ordered ``[lon, lat]`` pairs. ``total_miles`` is the road
    distance reported by the router; polyline lengths are rescaled to it so that route
    miles used for fuel maths agree with the distance reported to the client.
    """

    def __init__(self, coordinates, total_miles: float, spacing_miles: float = DEFAULT_SPACING_MILES):
        pts = np.asarray(coordinates, dtype=float)
        if pts.ndim != 2 or pts.shape[0] < 2 or pts.shape[1] < 2:
            raise ValueError("A route needs at least two [lon, lat] coordinates")
        lon, lat = pts[:, 0], pts[:, 1]

        seg = haversine_miles(lat[:-1], lon[:-1], lat[1:], lon[1:])
        cum = np.concatenate(([0.0], np.cumsum(seg)))
        polyline_miles = float(cum[-1])
        self.total_miles = float(total_miles)
        self._scale = self.total_miles / polyline_miles if polyline_miles > 0 else 1.0

        keep = self._decimate(cum, spacing_miles)
        self._lon, self._lat = lon[keep], lat[keep]
        self._cum = cum[keep] * self._scale
        # Sparse subset of the vertices (always including both ends) for the first-pass filter.
        coarse = self._decimate(self._cum, _COARSE_SPACING_MILES)
        self._coarse_lon, self._coarse_lat = self._lon[coarse], self._lat[coarse]
        self._coarse_max_gap = float(np.diff(self._cum[coarse]).max())
        self.min_lat, self.max_lat = float(self._lat.min()), float(self._lat.max())
        self.min_lon, self.max_lon = float(self._lon.min()), float(self._lon.max())

    @staticmethod
    def _decimate(cum: np.ndarray, spacing: float) -> np.ndarray:
        if cum[-1] <= 0:
            return np.array([0, len(cum) - 1])
        targets = np.arange(0.0, cum[-1], spacing)
        idx = np.searchsorted(cum, targets, side="left")
        return np.unique(np.concatenate((idx, [len(cum) - 1])))

    def match(self, lats, lons, max_offset_miles: float) -> list[RouteMatch]:
        """Return every point within ``max_offset_miles`` of the route, with its position."""
        lats = np.asarray(lats, dtype=float)
        lons = np.asarray(lons, dtype=float)

        # Cheap bounding-box prefilter before the exact point-to-segment computation.
        pad_lat = max_offset_miles / MILES_PER_DEGREE_LAT
        widest_lat = min(89.0, max(abs(self.min_lat), abs(self.max_lat)) + pad_lat)
        pad_lon = max_offset_miles / (MILES_PER_DEGREE_LAT * np.cos(np.radians(widest_lat)))
        in_box = (
            (lats >= self.min_lat - pad_lat)
            & (lats <= self.max_lat + pad_lat)
            & (lons >= self.min_lon - pad_lon)
            & (lons <= self.max_lon + pad_lon)
        )
        candidates = np.flatnonzero(in_box)
        candidates = candidates[self._near_coarse_vertices(lats[candidates], lons[candidates], max_offset_miles)]

        matches: list[RouteMatch] = []
        for start in range(0, len(candidates), _CHUNK):
            block = candidates[start : start + _CHUNK]
            along, offset = self._project(lats[block], lons[block])
            for i in np.flatnonzero(offset <= max_offset_miles):
                matches.append(RouteMatch(int(block[i]), float(along[i]), float(offset[i])))
        matches.sort(key=lambda m: m.route_mile)
        return matches

    def _near_coarse_vertices(self, lats: np.ndarray, lons: np.ndarray, max_offset_miles: float) -> np.ndarray:
        """Cheap, conservative filter: keep points that could be within the corridor.

        A point within ``max_offset`` of the route is within ``max_offset + gap / 2`` of the
        nearest sparse vertex (``gap`` = longest stretch between sparse vertices). The 5% margin
        covers the flat-earth approximation, so this never rejects a true match.
        """
        limit = (max_offset_miles + self._coarse_max_gap / 2) * 1.05
        keep = np.zeros(len(lats), dtype=bool)
        for start in range(0, len(lats), 2048):
            sl = slice(start, start + 2048)
            cos_lat = np.cos(np.radians(lats[sl]))[:, None]
            x = (self._coarse_lon[None, :] - lons[sl, None]) * cos_lat * MILES_PER_DEGREE_LAT
            y = (self._coarse_lat[None, :] - lats[sl, None]) * MILES_PER_DEGREE_LAT
            keep[sl] = (x * x + y * y).min(axis=1) <= limit * limit
        return keep

    def _project(self, lats: np.ndarray, lons: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Exact distance from each point to the polyline and its position along it.

        Works in a local flat plane centred on each point (x east, y north, in miles), which
        is accurate to well under 0.1% for segments of a mile or two.
        """
        cos_lat = np.cos(np.radians(lats))[:, None]
        x = (self._lon[None, :] - lons[:, None]) * cos_lat * MILES_PER_DEGREE_LAT
        y = (self._lat[None, :] - lats[:, None]) * MILES_PER_DEGREE_LAT

        x0, y0 = x[:, :-1], y[:, :-1]
        dx, dy = x[:, 1:] - x0, y[:, 1:] - y0
        seg_len_sq = dx * dx + dy * dy
        # Closest point on each segment to the origin (the station), clamped to the segment.
        with np.errstate(divide="ignore", invalid="ignore"):
            t = np.where(seg_len_sq > 0, -(x0 * dx + y0 * dy) / seg_len_sq, 0.0)
        t = np.clip(t, 0.0, 1.0)
        dist = np.hypot(x0 + t * dx, y0 + t * dy)

        best = np.argmin(dist, axis=1)
        rows = np.arange(len(lats))
        seg_miles = self._cum[1:] - self._cum[:-1]
        along = np.minimum(self._cum[best] + t[rows, best] * seg_miles[best], self.total_miles)
        return along, dist[rows, best]
