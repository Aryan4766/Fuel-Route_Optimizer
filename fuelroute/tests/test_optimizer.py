"""Optimiser correctness, including a comparison against an independent exhaustive DP."""
import random

import pytest

from fuelroute.exceptions import InfeasibleRoute
from fuelroute.services.optimizer import Candidate, optimize_fuel_stops

RANGE, MPG = 500.0, 10.0  # 50-gallon tank


def cand(mile, price, offset=0.0):
    return Candidate(mile, offset, price, ref=f"s@{mile}")


def reference_min_cost(route_miles, stations, initial_gal, capacity_gal=50, mpg=10):
    """Independent exact DP over whole gallons (all test distances are multiples of `mpg`).

    state = (station index, gallons in tank on arrival). Buy one gallon at a time.
    """
    from functools import lru_cache

    pts = sorted(stations)  # (mile, price)
    n = len(pts)

    @lru_cache(maxsize=None)
    def best(i, fuel):  # i = index of the station we're at (-1 start), fuel on arrival
        here = 0 if i < 0 else pts[i][0]
        price = 0 if i < 0 else pts[i][1]
        options = []
        # Choose how much to have in the tank when leaving, then where to go next.
        lo = fuel
        hi = fuel if i < 0 else capacity_gal
        for leave in range(lo, hi + 1):
            buy_cost = (leave - fuel) * price
            # Go straight to the destination
            need = round((route_miles - here) / mpg)
            if need <= leave:
                options.append(buy_cost)
            for j in range(i + 1, n):
                need = round((pts[j][0] - here) / mpg)
                if need <= leave:
                    options.append(buy_cost + best(j, leave - need))
        return min(options) if options else float("inf")

    return best(-1, initial_gal)


def test_short_trip_needs_no_stops():
    plan = optimize_fuel_stops(300, [cand(100, 3.0)], range_miles=RANGE, mpg=MPG)
    assert plan.stops == []
    assert plan.total_cost == 0
    assert plan.fuel_consumed_gallons == pytest.approx(30)
    assert plan.ending_fuel_gallons == pytest.approx(20)


def test_buys_only_what_is_needed_at_destination():
    # 800 mi trip, full 50 gal start, one station at mile 400 priced $3.
    plan = optimize_fuel_stops(800, [cand(400, 3.0)], range_miles=RANGE, mpg=MPG)
    (stop,) = plan.stops
    assert stop.fuel_on_arrival == pytest.approx(10)  # 50 - 400/10
    assert stop.gallons_purchased == pytest.approx(30)  # 40 gal still needed - 10 in the tank
    assert plan.ending_fuel_gallons == pytest.approx(0, abs=1e-9)  # nothing wasted
    assert plan.fuel_consumed_gallons == pytest.approx(80)
    assert plan.fuel_purchased_gallons == pytest.approx(30)  # consumed 80 = 50 initial + 30 bought
    assert plan.total_cost == pytest.approx(90.0)


def test_prefers_cheap_station_and_buys_just_enough_at_expensive_one():
    # Expensive station first, cheap station later; must buy minimum at the expensive one.
    stations = [cand(450, 4.00), cand(900, 3.00)]
    plan = optimize_fuel_stops(1300, stations, range_miles=RANGE, mpg=MPG)
    by_mile = {s.candidate.route_mile: s for s in plan.stops}
    # At 450 the tank holds 5 gal; mile 900 is 45 gal away -> buy 40 at $4.
    assert by_mile[450].gallons_purchased == pytest.approx(40)
    # Arrive at 900 with 0 gal; 400 miles (40 gal) remain -> buy 40 at $3.
    assert by_mile[900].gallons_purchased == pytest.approx(40)
    assert plan.total_cost == pytest.approx(40 * 4 + 40 * 3)


def test_fills_tank_at_cheapest_when_nothing_cheaper_ahead():
    stations = [cand(300, 3.00), cand(700, 3.50), cand(1100, 3.40)]
    plan = optimize_fuel_stops(1500, stations, range_miles=RANGE, mpg=MPG)
    first = plan.stops[0]
    assert first.candidate.route_mile == 300
    assert first.fuel_after_purchase == pytest.approx(50)  # topped up fully


def test_skips_station_not_worth_a_stop():
    # The only reason to stop at 100 would be price, but 120 is cheaper and reachable.
    plan = optimize_fuel_stops(900, [cand(100, 5.0), cand(450, 3.0)], range_miles=RANGE, mpg=MPG)
    assert [s.candidate.route_mile for s in plan.stops] == [450]


def test_infeasible_gap_raises():
    with pytest.raises(InfeasibleRoute) as err:
        optimize_fuel_stops(2000, [cand(100, 3.0)], range_miles=RANGE, mpg=MPG)
    assert "No fuel station" in err.value.message


def test_no_stations_but_trip_longer_than_range_raises():
    with pytest.raises(InfeasibleRoute):
        optimize_fuel_stops(600, [], range_miles=RANGE, mpg=MPG)


def test_exact_range_boundary_is_reachable():
    plan = optimize_fuel_stops(1000, [cand(500, 3.0)], range_miles=RANGE, mpg=MPG)
    assert plan.stops[0].fuel_on_arrival == pytest.approx(0)
    assert plan.ending_fuel_gallons == pytest.approx(0, abs=1e-9)


def test_partial_initial_fuel_limits_first_hop():
    with pytest.raises(InfeasibleRoute):
        optimize_fuel_stops(900, [cand(300, 3.0)], range_miles=RANGE, mpg=MPG, initial_fuel_gallons=20)
    plan = optimize_fuel_stops(600, [cand(150, 3.0)], range_miles=RANGE, mpg=MPG, initial_fuel_gallons=20)
    assert plan.stops[0].fuel_on_arrival == pytest.approx(5)
    assert plan.initial_fuel_gallons == 20


def test_empty_start_requires_station_at_start():
    plan = optimize_fuel_stops(300, [cand(0, 3.0)], range_miles=RANGE, mpg=MPG, initial_fuel_gallons=0)
    assert plan.stops[0].gallons_purchased == pytest.approx(30)


def test_zero_length_route():
    plan = optimize_fuel_stops(0, [], range_miles=RANGE, mpg=MPG)
    assert plan.stops == [] and plan.total_cost == 0


def test_stations_beyond_route_or_with_negative_position_are_ignored():
    plan = optimize_fuel_stops(300, [cand(-5, 1.0), cand(400, 1.0)], range_miles=RANGE, mpg=MPG)
    assert plan.stops == []


def test_equal_prices_prefer_fewest_stops_and_stay_feasible():
    stations = [cand(m, 3.0) for m in range(100, 1500, 100)]
    plan = optimize_fuel_stops(1500, stations, range_miles=RANGE, mpg=MPG)
    # 1500 mi = 150 gal; 50 free at the start, so exactly 100 gal are bought at $3.
    assert plan.fuel_purchased_gallons == pytest.approx(100)
    assert plan.total_cost == pytest.approx(300.0)
    assert_plan_is_valid(plan, 1500, 10, 50)


def assert_plan_is_valid(plan, route_miles, mpg, capacity):
    """Replay the plan: the tank never goes negative or overflows; accounting balances."""
    fuel, position, exit_offset = plan.initial_fuel_gallons, 0.0, 0.0
    purchased = 0.0
    for stop in plan.stops:
        c = stop.candidate
        leg = (c.route_mile - position) + exit_offset + c.offset_miles
        fuel -= leg / mpg
        assert fuel >= -1e-6, "ran out of fuel before a stop"
        assert fuel == pytest.approx(stop.fuel_on_arrival, abs=1e-6)
        assert stop.gallons_purchased > 0
        fuel += stop.gallons_purchased
        assert fuel <= capacity + 1e-6, "tank overflow"
        purchased += stop.gallons_purchased
        position, exit_offset = c.route_mile, c.offset_miles
    fuel -= ((route_miles - position) + exit_offset) / mpg
    assert fuel >= -1e-6, "ran out of fuel before the destination"
    assert fuel == pytest.approx(plan.ending_fuel_gallons, abs=1e-6)
    # fuel purchased + fuel we started with - fuel left == fuel burned
    assert purchased + plan.initial_fuel_gallons - plan.ending_fuel_gallons == pytest.approx(
        plan.fuel_consumed_gallons, abs=1e-6
    )


@pytest.mark.parametrize("seed", range(40))
def test_matches_exhaustive_dp_on_random_instances(seed):
    rng = random.Random(seed)
    route = rng.randrange(60, 220) * 10  # miles, multiple of 10 => whole gallons
    n = rng.randrange(1, 12)
    miles = sorted(rng.sample(range(1, route // 10), min(n, route // 10 - 1)))
    stations = [(m * 10, rng.randrange(250, 450)) for m in miles]  # price in cents
    initial = rng.choice([50, 30, 50])

    candidates = [Candidate(m, 0.0, p / 100, ref=m) for m, p in stations]
    expected = reference_min_cost(route, tuple(stations), initial)
    if expected == float("inf"):
        with pytest.raises(InfeasibleRoute):
            optimize_fuel_stops(route, candidates, range_miles=RANGE, mpg=MPG, initial_fuel_gallons=initial)
        return
    plan = optimize_fuel_stops(route, candidates, range_miles=RANGE, mpg=MPG, initial_fuel_gallons=initial)
    assert_plan_is_valid(plan, route, 10, 50)
    assert plan.total_cost * 100 == pytest.approx(expected, abs=1e-6)


@pytest.mark.parametrize("seed", range(25))
def test_plans_with_detours_are_always_feasible(seed):
    rng = random.Random(1000 + seed)
    route = rng.uniform(800, 3000)
    miles = sorted(rng.uniform(0, route) for _ in range(rng.randrange(20, 60)))
    candidates = [Candidate(m, rng.uniform(0, 10), rng.uniform(2.7, 4.5), ref=m) for m in miles]
    try:
        plan = optimize_fuel_stops(route, candidates, range_miles=RANGE, mpg=MPG)
    except InfeasibleRoute:
        return
    assert_plan_is_valid(plan, route, 10, 50)
    # Every stop is within one tank of the previous one, measured on the real driven legs.
    assert plan.fuel_consumed_gallons >= route / 10 - 1e-9


def test_detour_fuel_is_counted_as_consumed():
    c = Candidate(400, 8.0, 3.0, ref="x")
    plan = optimize_fuel_stops(800, [c], range_miles=RANGE, mpg=MPG)
    assert plan.fuel_consumed_gallons == pytest.approx((800 + 16) / 10)


def test_zero_purchase_stops_are_pruned():
    # Station at 100 is only "cheaper" than the start; the full tank covers everything.
    plan = optimize_fuel_stops(400, [cand(100, 2.0)], range_miles=RANGE, mpg=MPG)
    assert plan.stops == []


@pytest.mark.parametrize("kwargs", [{"range_miles": 0}, {"mpg": 0}, {"initial_fuel_gallons": 99}])
def test_invalid_parameters(kwargs):
    with pytest.raises(ValueError):
        optimize_fuel_stops(100, [], **{"range_miles": RANGE, "mpg": MPG, **kwargs})
