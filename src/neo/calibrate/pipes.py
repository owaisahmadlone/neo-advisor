"""Fit each line's friction coefficient K from historian pressures and flows.

The liquid-head term (L, beta) keeps its registry prior; only K is refitted, by least
squares on  u_in - e^s u_out - L/(1 + beta q) = K q^2. The fit is bounded to a band
around the registry value, and a residual that is too large is flagged.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from neo.physics.pipes import PipeHydraulics

K_BAND = (0.5, 2.0)
MIN_POINTS = 8


@dataclass(frozen=True, slots=True)
class PipeFit:
    hydraulics: PipeHydraulics
    points: int
    rel_residual: float
    clipped: bool


def fit_friction(prior: PipeHydraulics, p_in: np.ndarray, p_out: np.ndarray, q: np.ndarray) -> PipeFit:
    mask = np.isfinite(p_in) & np.isfinite(p_out) & np.isfinite(q) & (q > 1.0)
    p_in, p_out, q = p_in[mask], p_out[mask], q[mask]
    if len(q) < MIN_POINTS:
        return PipeFit(prior, len(q), float("nan"), clipped=False)
    target = p_in**2 - prior.outlet_factor * p_out**2 - prior.liquid_head / (1 + prior.holdup_beta * q)
    k = float(np.sum(target * q**2) / np.sum(q**4))
    lo, hi = (prior.k_friction * f for f in K_BAND)
    clipped = not lo <= k <= hi
    k = min(max(k, lo), hi)
    fitted = replace(prior, k_friction=k)
    predicted = np.array([fitted.dp2(x) for x in q]) + prior.outlet_factor * p_out**2
    residual = float(np.sqrt(np.mean((predicted - p_in**2) ** 2)) / np.mean(p_in**2))
    return PipeFit(fitted, len(q), residual, clipped)
