"""Fuel-stop optimisation.

Problem
-------
A vehicle drives a route of ``R`` miles. Its tank holds ``C = range / mpg`` gallons, so it
can cover at most ``range`` miles on a full tank. Fuel stations sit at known points along
the route, each with a price per gallon, and fuel may be bought in any (fractional)
quantity. Choose where to stop and how much to buy so that the total money spent is
minimal and the tank never runs dry.

Algorithm
---------
This is the classic *gas station problem* on a line. The greedy rule below is optimal:

  At a station ``i`` with price ``p_i``, look at every station reachable on a full tank.
    * If one of them is strictly cheaper, drive to the *nearest* such station and buy only
      the fuel still missing to get there (buying more here would mean paying ``p_i`` for
      fuel that could be bought cheaper later).
    * Otherwise nothing within reach is cheaper, so fill the tank completely (every gallon
      bought here is at least as cheap as anything reachable) and drive to the cheapest
      station in reach (the farthest one on ties).

The destination is modelled as a station with price 0, which makes the vehicle buy exactly
what it needs to finish and no more. The start is a "station" of price 0 whose capacity is
the initial fuel in the tank; that fuel is free (assumed already paid for).

Optimality
----------
With every station exactly on the route (zero detour) this greedy rule is the standard
optimal solution of the gas-station problem with continuous fuel and per-station prices
(buying a gallon earlier than necessary is only worthwhile if nothing cheaper is reachable
before the tank would otherwise run low). The test-suite checks it against an independent
exhaustive DP on several hundred random instances, including price ties and every starting
fuel level; no mismatch was found. That is empirical support on top of the standard result,
not a fresh proof.

Detours
-------
Stations are usually slightly off the route. Visiting station ``i`` costs ``offset_i``
extra miles to reach it and ``offset_i`` to rejoin the route, so the driving distance of a
leg between consecutive stops ``i -> j`` is ``(s_j - s_i) + offset_i + offset_j``. That
exact distance is used for reachability and for fuel burned, so every plan returned is
physically feasible and the detour fuel is paid for.

With non-zero offsets the problem has a fixed charge per visited station and the greedy
rule alone is *not* optimal: it can pick a station for a tiny price advantage even though
its detour wastes more fuel. Two refinements repair most of that: ``_drop_stops_from_pool``
(re-plan with a detouring stop removed from the whole pool) and ``_refine`` (a local search over
the set of stops with drop, add and swap moves, each trial re-planned on that small set).

Measured against an exact LP over every subset of stops (``scripts/audit_detour_optimality.py``,
10-mile offsets): plain greedy was optimal in 49 of 90 trips (mean gap $1.92, worst $10.21);
with the refinements 90 of 90 on that same set, and on 196 further feasible instances that were
never used while tuning, 193 optimal with a mean gap of $0.032 and a worst gap of $3.54. So with
detours the result is a very good feasible plan, not a proven global optimum.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..exceptions import InfeasibleRoute

_EPS = 1e-9
_MIN_PURCHASE_GALLONS = 1e-6
_MAX_REFINEMENTS = 50
_SWAP_WINDOW_MILES = 150.0  # how far from a stop a replacement station may be


@dataclass(frozen=True, slots=True)
class Candidate:
    """A station that can be used for refuelling."""

    route_mile: float  # position along the route (miles from the start)
    offset_miles: float  # straight-line distance from the route
    price: float  # dollars per gallon
    ref: Any = None  # caller's handle for the station (not used by the algorithm)


@dataclass(frozen=True, slots=True)
class PlannedStop:
    candidate: Candidate
    gallons_purchased: float
    fuel_on_arrival: float  # gallons in the tank when reaching the station
    fuel_after_purchase: float

    @property
    def cost(self) -> float:
        return self.gallons_purchased * self.candidate.price


@dataclass(frozen=True, slots=True)
class FuelPlan:
    stops: list[PlannedStop] = field(default_factory=list)
    initial_fuel_gallons: float = 0.0
    ending_fuel_gallons: float = 0.0
    fuel_consumed_gallons: float = 0.0  # includes fuel burned on detours to stations

    @property
    def fuel_purchased_gallons(self) -> float:
        return sum(s.gallons_purchased for s in self.stops)

    @property
    def total_cost(self) -> float:
        return sum(s.cost for s in self.stops)


@dataclass(slots=True)
class _Node:
    route_mile: float
    offset: float
    price: float
    candidate: Candidate | None = None

    @property
    def entry(self) -> float:  # odometer position when arriving at this node
        return self.route_mile + self.offset

    @property
    def exit(self) -> float:  # odometer position when leaving this node
        return self.route_mile - self.offset


def optimize_fuel_stops(
    route_miles: float,
    candidates: list[Candidate],
    *,
    range_miles: float = 500.0,
    mpg: float = 10.0,
    initial_fuel_gallons: float | None = None,
) -> FuelPlan:
    """Return the cheapest feasible refuelling plan.

    ``initial_fuel_gallons`` defaults to a full tank. Raises ``InfeasibleRoute`` when the
    stations (plus the starting fuel) cannot carry the vehicle to the destination.
    """
    if route_miles < 0 or range_miles <= 0 or mpg <= 0:
        raise ValueError("route_miles must be >= 0; range_miles and mpg must be > 0")
    capacity = range_miles / mpg
    initial = capacity if initial_fuel_gallons is None else initial_fuel_gallons
    if not 0 <= initial <= capacity + _EPS:
        raise ValueError("initial_fuel_gallons must be between 0 and the tank capacity")
    initial = min(initial, capacity)

    pool = sorted(
        (c for c in candidates if 0 <= c.route_mile <= route_miles and c.price >= 0),
        key=lambda c: (c.route_mile, c.price),
    )

    full_pool = pool
    plan, pool = _solve(route_miles, pool, capacity, mpg, initial)
    plan, _ = _drop_stops_from_pool(route_miles, pool, plan, capacity, mpg, initial)
    return _refine(route_miles, full_pool, plan, capacity, mpg, initial)


def _solve(route_miles, pool, capacity, mpg, initial):
    """Greedy plan, re-planned without any stop that ends up buying nothing."""
    while True:
        plan, useless = _greedy(route_miles, pool, capacity, mpg, initial)
        if not useless:
            return plan, pool
        pool = [c for c in pool if id(c) not in useless]


def _drop_stops_from_pool(route_miles, pool, plan, capacity, mpg, initial):
    """Re-plan with a detouring stop removed from the *whole* pool when that is cheaper.

    Unlike the subset search below, greedy may then pick a different nearby station in its
    place, which reaches plans the subset moves cannot.
    """
    for _ in range(_MAX_REFINEMENTS):
        for stop in plan.stops:
            if stop.candidate.offset_miles <= _EPS:
                continue  # without a detour there is nothing to save by skipping the stop
            trial_pool = [c for c in pool if c is not stop.candidate]
            try:
                trial, trial_pool = _solve(route_miles, trial_pool, capacity, mpg, initial)
            except InfeasibleRoute:
                continue
            if trial.total_cost < plan.total_cost - 1e-9:
                plan, pool = trial, trial_pool
                break
        else:
            break
    return plan, pool


def _refine(route_miles, pool, plan, capacity, mpg, initial):
    """Local search over the *set of stops*, for plans where detours matter.

    The greedy rule compares prices only, so when stations are off the route it can pick a
    stop whose detour costs more than its price advantage saves (a fixed charge per visited
    station). Each round tries, on the current stop set, every single drop, every single
    addition from the pool and every swap with a nearby station. Each trial is evaluated by
    re-planning on just that small set, and the best strictly cheaper plan is kept. Costs fall
    every round, so the loop terminates. This is a heuristic: it never returns a plan worse
    than plain greedy but does not guarantee the global optimum.
    """
    if all(c.offset_miles <= _EPS for c in pool):
        return plan  # no detours: greedy is already optimal

    for _ in range(_MAX_REFINEMENTS):
        members = [stop.candidate for stop in plan.stops]
        member_ids = {id(c) for c in members}
        outsiders = [c for c in pool if id(c) not in member_ids]
        best = plan

        def consider(subset):
            """Evaluate one trial set; True when it beats the current plan."""
            nonlocal best
            try:
                trial, _ = _solve(route_miles, sorted(subset, key=lambda c: c.route_mile), capacity, mpg, initial)
            except InfeasibleRoute:
                return False
            if trial.total_cost < best.total_cost - 1e-9:
                best = trial
                return True
            return False

        def moves():
            for m in members:
                others = [c for c in members if c is not m]
                yield others
                for c in outsiders:
                    if abs(c.route_mile - m.route_mile) <= _SWAP_WINDOW_MILES:
                        yield others + [c]
            for c in outsiders:
                yield members + [c]

        any(consider(subset) for subset in moves())  # first improvement per round

        if best is plan:
            break
        plan = best
    return plan


def _greedy(route_miles, pool, capacity, mpg, initial):
    nodes = [_Node(0.0, 0.0, 0.0)]
    nodes += [_Node(c.route_mile, c.offset_miles, c.price, c) for c in pool]
    nodes.append(_Node(route_miles, 0.0, 0.0))
    last = len(nodes) - 1

    stops: list[PlannedStop] = []
    consumed = 0.0
    i, fuel = 0, initial
    while i != last:
        here = nodes[i]
        # The start can only "fill" what the tank already holds; stations fill to capacity.
        reach_gal = initial if i == 0 else capacity
        reach_miles = reach_gal * mpg
        in_reach = []
        for j in range(i + 1, last + 1):
            if nodes[j].route_mile - here.route_mile > reach_miles + _EPS:
                break  # the leg is at least as long as the straight-line gap
            if nodes[j].entry - here.exit <= reach_miles + _EPS:
                in_reach.append(j)
        if not in_reach:
            gap = nodes[i + 1].entry - here.exit
            raise InfeasibleRoute(
                f"No fuel station within {reach_miles:.0f} miles after route mile "
                f"{here.route_mile:.0f}; the next stop is {gap:.0f} miles away.",
                {"route_mile": round(here.route_mile, 1), "next_gap_miles": round(gap, 1)},
            )

        if i == 0:
            nxt = last if last in in_reach else _cheapest(nodes, in_reach)
            buy = 0.0
        else:
            cheaper = next((j for j in in_reach if nodes[j].price < here.price - _EPS), None)
            if cheaper is not None:
                nxt = cheaper
                buy = max(0.0, (nodes[nxt].entry - here.exit) / mpg - fuel)
            else:
                nxt = _cheapest(nodes, in_reach)
                buy = capacity - fuel

        if i != 0:
            stops.append(PlannedStop(here.candidate, buy, fuel, fuel + buy))
        fuel += buy
        leg_gallons = (nodes[nxt].entry - here.exit) / mpg
        fuel -= leg_gallons
        consumed += leg_gallons
        if fuel < -1e-7:  # unreachable by construction; guards against logic regressions
            raise AssertionError("Plan ran the tank dry")
        fuel = max(fuel, 0.0)
        i = nxt

    useless = {id(s.candidate) for s in stops if s.gallons_purchased < _MIN_PURCHASE_GALLONS}
    return FuelPlan(stops, initial, fuel, consumed), useless


def _cheapest(nodes: list[_Node], indices: list[int]) -> int:
    """Lowest price among ``indices``; on ties the farthest along the route."""
    return min(indices, key=lambda j: (nodes[j].price, -j))
