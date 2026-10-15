"""Extra optimiser checks: stress against the exhaustive DP, detours, ties, boundaries."""
import random

import pytest

from fuelroute.exceptions import InfeasibleRoute
from fuelroute.services.optimizer import Candidate, _solve, optimize_fuel_stops
from fuelroute.tests.test_optimizer import (
    MPG,
    RANGE,
    assert_plan_is_valid,
    cand,
    reference_min_cost,
)


@pytest.mark.parametrize("seed", range(300))
def test_exhaustive_dp_with_ties_and_any_starting_fuel(seed):
    """Zero-detour instances: price ties, every starting fuel level, stations at mile 0 and the end."""
    rng = random.Random(50_000 + seed)
    route = rng.randrange(10, 260) * 10
    miles = sorted(rng.sample(range(0, route // 10 + 1), min(rng.randrange(0, 14), route // 10 + 1)))
    narrow = rng.random() < 0.4  # many equal / near-equal prices
    stations = [(m * 10, rng.randrange(280, 300) if narrow else rng.randrange(250, 450)) for m in miles]
    initial = rng.randrange(0, 51)
    candidates = [Candidate(m, 0.0, p / 100, ref=m) for m, p in stations]
    expected = reference_min_cost(route, tuple(stations), initial)
    kwargs = dict(range_miles=RANGE, mpg=MPG, initial_fuel_gallons=initial)
    if expected == float("inf"):
        with pytest.raises(InfeasibleRoute):
            optimize_fuel_stops(route, candidates, **kwargs)
        return
    plan = optimize_fuel_stops(route, candidates, **kwargs)
    assert_plan_is_valid(plan, route, 10, 50)
    assert plan.total_cost * 100 == pytest.approx(expected, abs=1e-6)


def test_detours_can_make_a_gap_unreachable():
    # Stations at route miles 10 and 490 (480 apart). With 8-mile offsets the real leg is
    # 480 + 8 + 8 = 496 <= 500; with 12-mile offsets it is 504 and cannot be driven.
    ok = [Candidate(10, 8.0, 3.0, "a"), Candidate(490, 8.0, 3.0, "b")]
    optimize_fuel_stops(980, ok, range_miles=RANGE, mpg=MPG, initial_fuel_gallons=1.8)
    too_far = [Candidate(10, 12.0, 3.0, "a"), Candidate(490, 12.0, 3.0, "b")]
    with pytest.raises(InfeasibleRoute):
        optimize_fuel_stops(980, too_far, range_miles=RANGE, mpg=MPG, initial_fuel_gallons=2.2)


def test_long_detour_for_a_tiny_saving_is_skipped():
    near = Candidate(400, 0.0, 3.00, "near")
    far = Candidate(420, 10.0, 2.99, "far")  # one cent cheaper but 20 detour miles (2 gallons)
    plan = optimize_fuel_stops(900, [near, far], range_miles=RANGE, mpg=MPG)
    assert [s.candidate.ref for s in plan.stops] == ["near"]


def test_worthwhile_detour_is_taken():
    near = Candidate(400, 0.0, 4.00, "near")
    far = Candidate(420, 10.0, 2.00, "far")
    plan = optimize_fuel_stops(900, [near, far], range_miles=RANGE, mpg=MPG)
    assert "far" in [s.candidate.ref for s in plan.stops]
    assert_plan_is_valid(plan, 900, 10, 50)


@pytest.mark.parametrize("seed", range(30))
def test_refinement_never_worse_than_plain_greedy(seed):
    rng = random.Random(9000 + seed)
    route = rng.uniform(900, 2200)
    miles = sorted(rng.uniform(0, route) for _ in range(rng.randrange(15, 50)))
    pool = [Candidate(m, rng.uniform(0, 10), rng.uniform(2.7, 4.5), i) for i, m in enumerate(miles)]
    try:
        base, _ = _solve(route, pool, 50.0, 10.0, 50.0)
    except InfeasibleRoute:
        return
    refined = optimize_fuel_stops(route, pool, range_miles=RANGE, mpg=MPG)
    assert refined.total_cost <= base.total_cost + 1e-9
    assert_plan_is_valid(refined, route, 10, 50)


def test_stations_at_start_and_destination():
    pool = [Candidate(0, 0, 1.0, "s"), Candidate(900, 0, 1.0, "e"), Candidate(450, 0, 3.0, "m")]
    plan = optimize_fuel_stops(900, pool, range_miles=RANGE, mpg=MPG)
    assert_plan_is_valid(plan, 900, 10, 50)


def test_equal_prices_give_deterministic_plan():
    stations = [cand(m, 3.0) for m in range(100, 1400, 100)]
    plans = [optimize_fuel_stops(1400, list(reversed(stations)), range_miles=RANGE, mpg=MPG) for _ in range(3)]
    assert len({tuple(s.candidate.route_mile for s in p.stops) for p in plans}) == 1
