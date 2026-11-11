"""Conservative piecewise-linear pieces for the MILP.

Every approximation here errs to the safe side, so a plan feasible in the linear model is
feasible in the nonlinear physics with the same parameters:

* concave well curves q(u): chords lie *below* the curve; q <= every chord line.
* convex pipe drops g(q): chords lie *above* the curve; du >= every chord line.
* compressor capacity: planes fitted then shifted *below* the true surface.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from itertools import pairwise

import numpy as np


@dataclass(frozen=True, slots=True)
class Line:
    slope: float
    intercept: float

    def __call__(self, x: float) -> float:
        return self.slope * x + self.intercept


def _chords(xs: Sequence[float], ys: Sequence[float]) -> list[Line]:
    lines = []
    for (x0, y0), (x1, y1) in pairwise(zip(xs, ys, strict=True)):
        slope = (y1 - y0) / (x1 - x0)
        lines.append(Line(slope, y0 - slope * x0))
    return lines


def adaptive_breakpoints(
    f: Callable[[float], float], lo: float, hi: float, *, tol: float, max_points: int = 40
) -> list[float]:
    """Breakpoints such that linear interpolation of ``f`` is within ``tol`` everywhere sampled."""
    points = [lo, hi]
    while len(points) < max_points:
        worst_gap, worst_x = 0.0, None
        for a, b in pairwise(points):
            fa, fb = f(a), f(b)
            for t in np.linspace(0.1, 0.9, 9):
                x = a + t * (b - a)
                gap = abs(f(x) - (fa + t * (fb - fa)))
                if gap > worst_gap:
                    worst_gap, worst_x = gap, x
        if worst_gap <= tol or worst_x is None:
            break
        points = sorted([*points, worst_x])
    return points


def chord_lines(f: Callable[[float], float], lo: float, hi: float, *, tol: float) -> list[Line]:
    xs = adaptive_breakpoints(f, lo, hi, tol=tol)
    return _chords(xs, [f(x) for x in xs])


@dataclass(frozen=True, slots=True)
class Plane:
    """value <= a + b * u_s + c * u_d (c is usually negative)."""

    a: float
    b: float
    c: float

    def __call__(self, u_s: float, u_d: float) -> float:
        return self.a + self.b * u_s + self.c * u_d


def conservative_planes(
    f: Callable[[float, float], float],
    *,
    ps_range: tuple[float, float],
    pd_range: tuple[float, float],
    ratio_max: float,
    bands: int = 4,
    grid: int = 15,
) -> list[Plane]:
    """Fit one plane per suction-pressure band in (u_s, u_d) over the feasible region
    (ps < pd <= ratio_max * ps), then shift all planes down until the minimum over planes
    never exceeds ``f`` on a dense grid of that region."""

    def feasible(ps: float, pd: float) -> bool:
        return ps < pd <= ratio_max * ps

    ps_edges = np.linspace(*ps_range, bands + 1)
    pd_values = np.linspace(*pd_range, grid)
    planes = []
    for lo, hi in pairwise(ps_edges):
        points = [(ps, pd) for ps in np.linspace(lo, hi, grid) for pd in pd_values if feasible(ps, pd)]
        if len(points) < 3:
            continue
        design = np.array([[1.0, ps**2, pd**2] for ps, pd in points])
        targets = np.array([f(ps, pd) for ps, pd in points])
        coef, *_ = np.linalg.lstsq(design, targets, rcond=None)
        planes.append(Plane(*map(float, coef)))

    dense = [
        (ps, pd)
        for ps in np.linspace(*ps_range, 4 * grid)
        for pd in np.linspace(*pd_range, 4 * grid)
        if feasible(ps, pd)
    ]
    worst = max(min(p(ps**2, pd**2) for p in planes) - f(ps, pd) for ps, pd in dense)
    shift = max(worst, 0.0)
    return [Plane(p.a - shift, p.b, p.c) for p in planes]
