"""Measure how far the optimiser is from the exact optimum when stations are off the route.

For small random instances this enumerates every subset of stations, solves the exact
fuel-purchase LP for each subset (legs include detour miles) and compares the cheapest of
them with the optimiser's plan.

Not part of the test-suite or runtime: it needs scipy.
    pip install scipy
    python scripts/audit_detour_optimality.py
"""
import itertools
import os
import random
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import linprog

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from fuelroute.exceptions import InfeasibleRoute  # noqa: E402
from fuelroute.services.optimizer import Candidate, optimize_fuel_stops  # noqa: E402

CAPACITY, MPG, START_FUEL = 50.0, 10.0, 50.0


def exact_cost_for_subset(route, stops):
    """Minimum purchase cost when the vehicle visits exactly ``stops`` (in route order)."""
    legs, position, previous_offset = [], 0.0, 0.0
    for stop in [*stops, None]:
        if stop is None:
            legs.append(((route - position) + previous_offset) / MPG)
        else:
            legs.append(((stop.route_mile - position) + previous_offset + stop.offset_miles) / MPG)
            position, previous_offset = stop.route_mile, stop.offset_miles
    if not stops:
        return 0.0 if legs[0] <= START_FUEL + 1e-9 else None
    n = len(stops)
    rows, limits = [], []
    for k in range(n):
        arrive = np.zeros(n)
        arrive[:k] = -1  # fuel on arrival at stop k >= 0
        rows.append(arrive)
        limits.append(START_FUEL - sum(legs[: k + 1]))
        fill = np.zeros(n)
        fill[: k + 1] = 1  # fuel after buying at stop k <= tank capacity
        rows.append(fill)
        limits.append(CAPACITY - START_FUEL + sum(legs[: k + 1]))
    rows.append(-np.ones(n))  # reach the destination
    limits.append(START_FUEL - sum(legs))
    result = linprog(
        [s.price for s in stops], A_ub=np.array(rows), b_ub=np.array(limits), bounds=[(0, None)] * n, method="highs"
    )
    return result.fun if result.status == 0 else None


def main(instances=150):
    gaps = []
    for seed in range(instances):
        rng = random.Random(seed)
        route = rng.uniform(900, 1800)
        n = rng.randrange(4, 10)
        miles = sorted(rng.uniform(0, route) for _ in range(n))
        pool = [Candidate(m, rng.uniform(0, 10), rng.uniform(2.8, 4.2), i) for i, m in enumerate(miles)]
        try:
            plan = optimize_fuel_stops(route, pool)
        except InfeasibleRoute:
            continue
        best = min(
            cost
            for r in range(n + 1)
            for subset in itertools.combinations(pool, r)
            if (cost := exact_cost_for_subset(route, list(subset))) is not None
        )
        gap = plan.total_cost - best
        assert gap >= -1e-6, f"optimiser beat the exact optimum on seed {seed}: impossible"
        gaps.append(gap)
    suboptimal = sum(g > 1e-6 for g in gaps)
    print(
        f"{len(gaps)} feasible instances: optimal in {len(gaps) - suboptimal}, "
        f"mean gap ${sum(gaps) / len(gaps):.3f}, worst gap ${max(gaps):.2f}"
    )


if __name__ == "__main__":
    main()
