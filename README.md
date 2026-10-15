<h1 align="center">Fuel Route Optimizer</h1>

<p align="center"><strong>Cost-optimised fuel stops along a US driving route, served by a REST API</strong></p>

<p align="center">
  <img alt="Python 3.12+" src="https://img.shields.io/badge/Python-3.12+-3776AB?style=flat&logo=python&logoColor=white">
  <img alt="Django 6.1" src="https://img.shields.io/badge/Django-6.1-0C4B33?style=flat&logo=django&logoColor=white">
  <img alt="DRF 3.18" src="https://img.shields.io/badge/DRF-3.18-A30000?style=flat">
  <img alt="NumPy 2.5" src="https://img.shields.io/badge/NumPy-2.5-013243?style=flat&logo=numpy&logoColor=white">
  <img alt="OSRM routing" src="https://img.shields.io/badge/Routing-OSRM-4C8BF5?style=flat">
  <img alt="Docker" src="https://img.shields.io/badge/Docker-ready-2496ED?style=flat&logo=docker&logoColor=white">
</p>

---

## About

A Django REST API that takes a start and finish location in the contiguous United States and returns:

* the driving route (GeoJSON geometry, distance, duration),
* the **cheapest set of fuel stops** along it for a vehicle with a 500-mile range and 10 MPG,
* gallons bought and cost at each stop, and the **total fuel cost**.

Stack: Django 6.1, Django REST Framework 3.18, numpy. Routing by [OSRM](https://project-osrm.org/), geocoding by [Nominatim](https://nominatim.org/) (both free, no API key).

---

## Contents

1. [Quick start](#quick-start)
2. [API](#api)
3. [How it works](#how-it-works)
4. [The fuel price data](#the-fuel-price-data)
5. [Optimisation algorithm](#optimisation-algorithm)
6. [Assumptions](#assumptions)
7. [External calls and caching](#external-calls-and-caching)
8. [Configuration](#configuration)
9. [Tests](#tests)
10. [Docker](#docker)
11. [Postman](#postman)
12. [Project layout](#project-layout)
13. [Limitations](#limitations)

---

## Quick start

Requires Python 3.12+ and internet access (for OSRM and Nominatim).

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                 # then put real contact info in HTTP_USER_AGENT
python manage.py runserver
```

The geocoded station file `data/stations_geocoded.csv` is committed, so nothing else is needed. Check it:

```bash
curl http://localhost:8000/api/health/
# {"status":"ok","stations_loaded":6626}

curl -X POST http://localhost:8000/api/route/ \
     -H "Content-Type: application/json" \
     -d '{"start": "Chicago, IL", "finish": "Dallas, TX"}'
```

Nominatim's usage policy requires an identifying `User-Agent`; set `HTTP_USER_AGENT` in `.env` to something with your contact address.

---

## API

### `POST /api/route/`

Request body (JSON):

| field    | type   | notes |
|----------|--------|-------|
| `start`  | string | Address / place name in the US (`"Chicago, IL"`), or `"lat,lon"` (`"41.8781,-87.6298"`). Max 200 characters. |
| `finish` | string | Same. Must differ from `start`. |

`lat,lon` input skips geocoding entirely.

Response `200` (geometry shortened here; real responses contain the full polyline):

```json
{
  "start":  {"query": "Chicago, IL", "label": "Chicago, ... Illinois, United States", "lat": 41.875562, "lon": -87.624421},
  "finish": {"query": "Dallas, TX",  "label": "Dallas, Dallas County, Texas, United States", "lat": 32.776272, "lon": -96.796856},
  "route": {
    "distance_miles": 966.92,
    "duration_seconds": 61574,
    "duration_hours": 17.1,
    "geometry": {"type": "LineString", "coordinates": [[-87.624351, 41.875563], [-87.624346, 41.875314], "..."]}
  },
  "fuel": {
    "vehicle": {"range_miles": 500.0, "mpg": 10.0, "tank_capacity_gallons": 50.0},
    "initial_fuel_gallons": 50.0,
    "fuel_consumed_gallons": 96.937,
    "detour_miles": 2.4,
    "fuel_purchased_gallons": 46.937,
    "ending_fuel_gallons": 0.0,
    "average_price_per_gallon": 2.89,
    "total_fuel_cost": 135.65,
    "currency": "USD"
  },
  "fuel_stops": [
    {
      "stop": 1,
      "station": {
        "opis_id": 69861, "name": "HUCKS FOOD & FUEL #379", "address": "I-57, EXIT 53",
        "city": "Marion", "state": "IL",
        "location": {"lat": 37.734445, "lon": -88.941164, "precision": "city_centroid", "source": "census_gazetteer_exact"}
      },
      "miles_from_start": 316.6,
      "miles_off_route": 0.9,
      "detour_miles": 1.9,
      "price_per_gallon": 2.929,
      "gallons_purchased": 30.612,
      "cost": 89.66,
      "fuel_on_arrival_gallons": 18.249,
      "fuel_after_purchase_gallons": 48.86
    },
    {
      "stop": 2,
      "station": {
        "opis_id": 68256, "name": "EXTRA MILE TRUCK STOP", "address": "I-30 EXIT 208",
        "city": "Hooks", "state": "TX",
        "location": {"lat": 33.470695, "lon": -94.286542, "precision": "city_centroid", "source": "census_gazetteer_exact"}
      },
      "miles_from_start": 804.0,
      "miles_off_route": 0.3,
      "detour_miles": 0.6,
      "price_per_gallon": 2.817,
      "gallons_purchased": 16.325,
      "cost": 45.99,
      "fuel_on_arrival_gallons": 0.0,
      "fuel_after_purchase_gallons": 16.325
    }
  ],
  "meta": {
    "stations_in_corridor": 188,
    "corridor_miles": 10.0,
    "external_api_calls": {"geocoding": 2, "routing": 1},
    "route_from_cache": false
  }
}
```

* `route.geometry` is a [GeoJSON](https://geojson.org/) `LineString` (`[lon, lat]` order). Paste `route.geometry` into [geojson.io](https://geojson.io), or hand it to Leaflet / Mapbox / Google Maps (`map.data.addGeoJson`) directly.
* **Three different quantities, never mixed:**
  * `fuel_consumed_gallons`: fuel *burned* = `(route.distance_miles + fuel.detour_miles) / mpg`.
  * `fuel_purchased_gallons`: fuel *bought* at stops. This is what is charged.
  * `ending_fuel_gallons`: fuel *left* at the destination.
  * They are related by `consumed = initial + purchased − ending`. The starting tank is free, so `purchased` is normally smaller than `consumed`.
* `detour_miles` is `2 × miles_off_route` summed over the stops (drive to the station and back to the route). Because station coordinates are city centres, much of this detour is an artefact of the approximate location, not real driving; it is included so that the range check stays conservative.
* **`location.precision: "city_centroid"`** on every station is a reminder that the coordinates are the centre of the station's city, **not the station's street position**. `source` says which dataset supplied them.
* `total_fuel_cost` is exactly the sum of the stop `cost` values; each `cost` is `gallons_purchased × price_per_gallon` rounded to the cent.
* `meta.external_api_calls` shows how many upstream calls *this* request made (0 when everything was cached).

### `GET /api/health/`

Returns `{"status": "ok", "stations_loaded": N}`.

### Errors

Every error has the same shape: `{"error": {"code": "...", "message": "...", "details": {...}}}`.

| HTTP | `code` | when |
|------|--------|------|
| 400 | `validation_error` | missing / blank / too-long field, identical start and finish |
| 400 | `parse_error` | body is not valid JSON |
| 404 | `location_not_found` | geocoder has no match for a location |
| 405 / 415 | `method_not_allowed` / `unsupported_media_type` | wrong verb / content type |
| 422 | `location_out_of_area` | a location is outside the contiguous US |
| 422 | `no_route` | OSRM cannot connect the points by road |
| 422 | `no_feasible_fuel_plan` | there is a stretch longer than the vehicle's range with no known station |
| 429 | `throttled` | rate limit exceeded (`API_THROTTLE_RATE`) |
| 502 | `upstream_error` | OSRM / Nominatim unreachable or returned garbage |
| 503 | `station_data_unavailable` | station CSV missing or unusable |
| 504 | `upstream_timeout` | OSRM / Nominatim exceeded the timeout |

---

## How it works

```
POST /api/route/
  1. validate input                                    (serializers.py)
  2. geocode start & finish        <= 2 Nominatim calls, cached   (services/geocoding.py)
  3. fetch driving route           <= 1 OSRM call, cached         (services/routing.py)
  4. find stations within 10 mi of the route, locally (numpy)     (services/geometry.py, stations.py)
  5. choose stops & quantities, locally                           (services/optimizer.py)
  6. format the response                                          (services/planner.py)
```

Station matching is vectorised: a bounding box and a sparse-vertex pre-filter discard most of the ~6,600 stations, then exact point-to-segment distances are computed for the survivors. The result is identical to projecting every station onto every segment (verified in the tests) and takes roughly half a second for a cross-country route. The stop-set local search adds about 0.2 s, so a cached Los Angeles to New York request completes in under a second.

---

## The fuel price data

`fuel-prices-for-be-assessment.csv` was inspected before any code was written:

| finding | detail |
|---------|--------|
| Columns | `OPIS Truckstop ID, Truckstop Name, Address, City, State, Rack ID, Retail Price` |
| Rows | 8,151, no missing values |
| **No coordinates** | There is no latitude/longitude, only street/exit text and city + state |
| Duplicates | 6,738 distinct IDs over 8,151 rows; 26 fully identical rows; 1,413 rows repeat an ID, often with different prices and slightly different name spellings (`PILOT #1243` vs `PILOT TRAVEL CENTER #1243`) |
| Non-US | 620 rows are Canadian (AB, BC, MB, NB, NS, ON, QC, SK, YT), several with very high prices |
| Prices | $2.69 – $6.40 per gallon, a handful above $5 |

Because routing needs coordinates, `python manage.py build_station_index` (already run, output committed) prepares `data/stations_geocoded.csv`:

1. **Dedupe** to one record per OPIS ID. When an ID has several prices the **lowest** is kept, since the point of the tool is to find the cheapest fuel. The CSV does not say what the multiple prices mean, so this is an assumption.
2. **Drop Canadian rows** (outside the routing area): 6,626 US stations remain.
3. **Geocode by city**: coordinates are the **centre of each station's city, an approximation and not the position of the gas station** (the CSV contains no coordinates). No values are fabricated:
   * 6,338 stations (6,296 + 42): matched to the [US Census Gazetteer](https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html) place centroid (public domain),
   * 288 stations: the Census file did not contain the place (e.g. unincorporated areas), so the city was looked up once in Nominatim (at 1 request/second, results kept in `data/nominatim_city_cache.json`).
   * Nothing is guessed: a station whose city cannot be resolved would be dropped and listed (currently none are).

The `geocode_source` column in the output records which method placed each station. To rebuild from scratch: `python manage.py build_station_index` (downloads the Gazetteer, ~4 minutes if the Nominatim cache is empty). Use `--skip-nominatim` for an offline, Census-only build.

---

## Optimisation algorithm

**Problem.** The vehicle drives `R` miles, holds `C = range / mpg = 50` gallons, burns 1 gallon per 10 miles, and may buy any fractional amount of fuel at stations with known prices. Minimise total money spent without ever running dry. This is the classic *gas station problem* on a line.

**Greedy rule.** At each station `i` with price `p`:

1. Look at all stations reachable on a **full tank**.
2. If some reachable station is **cheaper**, go to the *nearest* cheaper one and buy only the fuel that is still missing to get there. Paying `p` for more than that would be a waste, because cheaper fuel is available before the tank would run low.
3. Otherwise nothing in reach is cheaper, so every gallon here is at least as cheap as anything reachable: **fill the tank** and go to the cheapest station in reach (the farthest one on ties).

The destination is treated as a station with price 0, so the vehicle buys *exactly* what it needs to finish. The start is a price-0 "station" limited to the fuel the tank already holds. Stops that end up buying nothing are removed and the plan is recomputed, so every listed stop is a real purchase.

Complexity is `O(n · w)` for `n` candidate stations and `w` stations per tank-range window (a few hundred stations per request).

**What is and is not proven**

| situation | claim | basis |
|-----------|-------|-------|
| Stations exactly on the route (no detour) | **Optimal** | Standard result for the gas-station problem with continuous fuel. The tests also compare it with an independent exhaustive DP over whole gallons on 340 random instances (price ties, every starting fuel 0-50 gal, stations at the start and end) with no mismatch; a separate 3,000-instance run during the audit also had none. This is empirical support on top of the known result, not a new proof. |
| Stations off the route (the real case) | **Feasible and very good, not proven optimal** | A detour is a fixed charge per visited station, which breaks the greedy argument. Two refinements (re-plan without a detouring stop; a drop/add/swap local search over the set of stops) repair most of it. Against an exact LP over every subset of stops (`scripts/audit_detour_optimality.py`): plain greedy was optimal in 49 of 90 random trips (mean gap $1.92, worst $10.21); the refined plan in 90 of 90 on that set, and in **193 of 196** feasible trips on fresh seeds never used while tuning (mean gap $0.032, worst $3.54). |
| Every plan | **Feasible** | Tests replay each plan: the tank never goes below 0 or above capacity, every leg (including detours) is within range, the destination is reached, and `purchased + initial - ending = consumed`. |

Also tested: trip shorter than one tank (no stops), station exactly at the range limit, a gap longer than the range (reported as an error), a detour that pushes a leg past the range, partial and empty starting fuel, zero-length route, stations outside the route, stations at the start and at the destination, price ties.

**Detours.** Stopping at a station `d` miles off the route costs `d` miles to get there and `d` to rejoin. Those miles are included in the leg length for the range check and for the fuel burned, so each stop is genuinely reachable and the detour fuel is paid for. The price comparison itself ignores detours (hence the caveat above).

---

## Assumptions

* **Vehicle:** 500-mile range, 10 MPG, so a **50-gallon tank**. Partial fills are allowed.
* **Initial fuel:** the vehicle starts with a **full tank (50 gal)**. That fuel is treated as already paid for and **not charged**, so a trip shorter than 500 miles costs $0 and `total_fuel_cost` is the money spent at stations. Change `INITIAL_FUEL_FRACTION` to model a partially filled tank (e.g. `0.5`).
* **End of trip:** no fuel needs to remain at the destination. The plan buys only what is needed, so it usually arrives with ~0 gallons (see Limitations).
* **Stations on the route:** a station is a candidate if it is within `STATION_CORRIDOR_MILES` (default 10) of the route, measured in a straight line from the *city centre* where the station is assumed to be (see the data section).
* **Price:** the station's lowest listed price, assumed to apply to the whole trip.
* **Money:** `gallons × price` per stop, rounded to the cent; gallons are reported to 3 decimals.

---

## External calls and caching

Per **uncached** request: 2 Nominatim calls (one per place; none if you pass `lat,lon`) and **1 OSRM call**. Everything else (station matching, optimisation) runs locally.

* Geocoding results are cached for 30 days (key is the lower-cased, whitespace-normalised text); "not found" is cached for 1 hour.
* Routes are cached for 24 h, keyed on both coordinates rounded to ~10 m.
* An identical request (or any request reusing a cached place or route) therefore makes **0** external calls. `meta.external_api_calls` shows this.
* The station CSV is read once per process (`lru_cache`) into numpy arrays.
* Failures are never cached. Timeouts are configurable (`HTTP_CONNECT_TIMEOUT_SECONDS` default 5, `HTTP_READ_TIMEOUT_SECONDS` default 20). Connection failures and 502/503/504 replies are retried once; read timeouts are not, to fail fast, and surface as **504**. Worst case for one request is three upstream calls, so keep the server's own timeout above 3 x the read timeout (gunicorn is set to 120 s). Behind a reverse proxy the per-client rate limit sees the proxy's address unless you configure DRF's `NUM_PROXIES`.
* `CACHE_BACKEND=locmem` is per-process; use `filebased` or `redis` when running several workers (the Docker image uses `filebased`).

---

## Configuration

All settings are environment variables (or a `.env` file); see [`.env.example`](.env.example) for the full list with defaults. The important ones:

| variable | default | meaning |
|----------|---------|---------|
| `VEHICLE_RANGE_MILES` / `VEHICLE_MPG` | 500 / 10 | vehicle model |
| `INITIAL_FUEL_FRACTION` | 1.0 | how full the tank is at the start (free fuel) |
| `STATION_CORRIDOR_MILES` | 10 | how far from the route a station may be |
| `OSRM_BASE_URL`, `NOMINATIM_BASE_URL` | public servers | point at your own instances for production use |
| `HTTP_USER_AGENT` | placeholder | **set this** to include real contact info (Nominatim policy) |
| `CACHE_BACKEND`, `CACHE_LOCATION` | locmem | `locmem`, `filebased` or `redis` |
| `API_THROTTLE_RATE` | 60/min | per-client rate limit |
| `DJANGO_DEBUG`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS` | true / – / localhost | when `DEBUG` is false the key is mandatory, at least 32 characters, and may not be the `.env.example` placeholder |

---

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

No internet is needed. Nominatim and OSRM are replaced by an in-memory fake for most API tests. A few tests (`test_http_layer.py`) talk to a throwaway server on `127.0.0.1` to exercise the real timeout, retry and bad-reply handling, and subprocess tests check that bad environment configuration fails at start-up.

| file | covers |
|------|--------|
| `test_optimizer.py`, `test_optimizer_audit.py` | optimality vs exhaustive DP, feasibility replay, detours, ties, boundaries |
| `test_la_ny_regression.py` | real Los Angeles to New York candidates: cost equals the exact LP optimum for those stations ($705.04, was $708.78), no token purchases, every stop worth its detour, local optimality on random detour instances |
| `test_api.py`, `test_api_audit.py` | request/response, JSON validity, validation, every error status (400/404/405/415/422/429/502/503/504), caching and call counts, fuel accounting identities, city-centroid flag |
| `test_http_layer.py` | real-socket timeouts (504, no retry), one retry on 5xx, non-JSON replies, unreachable host, configuration validation |
| `test_geometry_and_stations.py` | corridor matching, curved routes, pre-filter equals brute force, station file parsing and failure modes, the committed data file |
| `test_station_prep.py` | city-name normalisation, Gazetteer matching, CSV dedupe |

---

## Docker

```bash
docker build -t fuel-route-optimizer .
docker run --rm -p 8000:8000 \
  -e DJANGO_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(50))')" \
  -e HTTP_USER_AGENT="fuel-route-optimizer/1.0 (you@example.com)" \
  fuel-route-optimizer
```

Runs gunicorn (3 workers, 120 s timeout) as a non-root user with a file-based cache shared between workers and a `/api/health/` health check. `DJANGO_SECRET_KEY` must be a real key of 32+ characters. `DJANGO_ALLOWED_HOSTS` defaults to `localhost,127.0.0.1`; add `-e DJANGO_ALLOWED_HOSTS=your.host.name` when serving under another name.

---

## Postman

Import [`postman/fuel-route-optimizer.postman_collection.json`](postman/fuel-route-optimizer.postman_collection.json). It has a health check, three route requests (cross-country, mid-range, `lat,lon` input) and two error cases, with test scripts that assert the cost sum, GeoJSON geometry and per-leg range.

Manual setup: `POST http://localhost:8000/api/route/`, **Body → raw → JSON**:

```json
{ "start": "Los Angeles, CA", "finish": "New York, NY" }
```

For a map, copy `route.geometry` from the response into [geojson.io](https://geojson.io).

---

## Project layout

```
config/                  Django settings (env-driven), urls, wsgi
fuelroute/
  views.py serializers.py urls.py exceptions.py
  services/
    geocoding.py         Nominatim client + cache
    routing.py           OSRM client + cache
    http.py              shared session, timeouts, error mapping
    geometry.py          haversine, route polyline, corridor matching
    stations.py          CSV loading and station index
    optimizer.py         the fuel-stop algorithm (no I/O)
    planner.py           orchestration and response building
    station_prep.py      offline data preparation helpers
  management/commands/build_station_index.py
  tests/
scripts/audit_detour_optimality.py  optional: compares the optimiser with an exact LP (needs scipy)
data/stations_geocoded.csv          prepared station data (committed)
data/nominatim_city_cache.json      cached fallback geocodes (committed)
fuel-prices-for-be-assessment.csv   original input
postman/                            Postman collection
```

---

## Limitations

* **Station locations are city centres, not street addresses.** The source CSV has no coordinates. A station can therefore be a few miles from where it is plotted; the 10-mile corridor absorbs this, and the reported `miles_off_route` is the distance from the city centre, not the exit. Using a street-level geocoder for all 6,626 stations (≈2 hours at Nominatim's rate limit) or a commercial dataset would remove this.
* **Zero reserve.** The plan is optimal for the stated range, so it frequently arrives at stations with an almost empty tank. Lower `VEHICLE_RANGE_MILES` (e.g. 450) to build in a safety margin.
* **Prices are a snapshot** and are assumed constant; the file's multiple prices per station are collapsed to the lowest.
* **Not proven optimal when stations are off the route** (see the table above). Detour miles count towards range and fuel burned but not towards the price comparison.
* **Centroid detours inflate fuel burned.** Detour miles come from city-centre offsets, so `fuel_consumed_gallons` can exceed `distance / mpg` by a few gallons on long trips (about 2.4 gal on Los Angeles to New York).
* **Out-and-back or looping routes:** a station is positioned at its nearest point on the route, which is ambiguous if the route passes the same place twice. Ordinary A-to-B driving routes are unaffected.
* **Straight-line corridor**, not road-network distance to the station.
* **Public OSRM / Nominatim are rate-limited demo servers** and may be slow or unavailable (the API returns 502/504). Self-host both for production.
* Routes outside the contiguous US are rejected; the price file has no Alaska or Hawaii stations.
* Starting fuel is free by assumption; a trip under 500 miles therefore costs $0.
* A cross-country response is ~0.8 MB of JSON (~240 KB gzipped) because the full-resolution route geometry is returned.
* The default `locmem` cache is per-process and lost on restart.
