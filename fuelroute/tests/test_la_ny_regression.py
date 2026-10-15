"""Regression tests for the Los Angeles -> New York defect found in the final audit.

Before the fix the local search only re-ran greedy with one detouring station removed from the
whole pool, so it kept stops that cost more in detour fuel than they saved: three of the eleven
stops bought only 1.9-2.1 gallons. Exact re-pricing showed that skipping them lowered the cost,
and the shipped plan cost $708.78 against a true optimum of $705.04 for the same stations.
"""
import json
import random
from pathlib import Path

import pytest

from fuelroute.exceptions import InfeasibleRoute
from fuelroute.services.optimizer import _SWAP_WINDOW_MILES, Candidate, _solve, optimize_fuel_stops
from fuelroute.tests.test_optimizer import RANGE, MPG, assert_plan_is_valid

FIXTURE = json.loads((Path(__file__).parent / "data" / "la_ny_candidates.json").read_text())
OLD_SHIPPED_COST = 708.78


@pytest.fixture(scope="module")
def la_ny():
    candidates = [
        Candidate(c["route_mile"], c["offset_miles"], c["price"], ref=c["city"]) for c in FIXTURE["candidates"]
    ]
    plan = optimize_fuel_stops(FIXTURE["route_miles"], candidates, range_miles=RANGE, mpg=MPG)
    return FIXTURE["route_miles"], candidates, plan


def test_cost_equals_exact_optimum_for_the_real_stations(la_ny):
    """`exact_optimum_cost` was computed offline by an LP over all 4,096 subsets of the 12 stations."""
    _, _, plan = la_ny
    assert plan.total_cost == pytest.approx(FIXTURE["exact_optimum_cost"], abs=1e-3)
    assert plan.total_cost < OLD_SHIPPED_COST - 3.0


def test_la_ny_plan_is_feasible_and_has_no_token_purchases(la_ny):
    route, _, plan = la_ny
    assert_plan_is_valid(plan, route, 10, 50)
    assert len(plan.stops) == 8
    assert min(s.gallons_purchased for s in plan.stops) > 5, "stops that buy a couple of gallons are not worth a detour"


def test_every_stop_is_worth_keeping(la_ny):
    """Dropping any single stop must be infeasible or more expensive."""
    route, _, plan = la_ny
    members = [s.candidate for s in plan.stops]
    for i, stop in enumerate(members):
        rest = members[:i] + members[i + 1 :]
        try:
            trial, _ = _solve(route, rest, 50.0, 10.0, 50.0)
        except InfeasibleRoute:
            continue
        assert trial.total_cost >= plan.total_cost - 1e-9, f"stop {i + 1} ({stop.ref}) is not worth its detour"


@pytest.mark.parametrize("seed", range(40))
def test_returned_plan_is_locally_optimal(seed):
    """No single drop, add or swap (re-planned on that small set) is cheaper than the result."""
    rng = random.Random(77_000 + seed)
    route = rng.uniform(900, 2600)
    miles = sorted(rng.uniform(0, route) for _ in range(rng.randrange(15, 45)))
    pool = [Candidate(m, rng.uniform(0, 10), rng.uniform(2.6, 4.4), i) for i, m in enumerate(miles)]
    try:
        plan = optimize_fuel_stops(route, pool, range_miles=RANGE, mpg=MPG)
    except InfeasibleRoute:
        return
    assert_plan_is_valid(plan, route, 10, 50)

    members = [s.candidate for s in plan.stops]
    outsiders = [c for c in pool if all(c is not m for m in members)]
    trials = []
    for m in members:
        others = [c for c in members if c is not m]
        trials.append(others)
        trials += [others + [c] for c in outsiders if abs(c.route_mile - m.route_mile) <= _SWAP_WINDOW_MILES]
    trials += [members + [c] for c in outsiders]
    for subset in trials:
        try:
            trial, _ = _solve(route, sorted(subset, key=lambda c: c.route_mile), 50.0, 10.0, 50.0)
        except InfeasibleRoute:
            continue
        assert trial.total_cost >= plan.total_cost - 1e-9
