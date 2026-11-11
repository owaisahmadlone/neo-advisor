"""Data reconciliation: adjust well rate estimates to satisfy meter balances.

Weighted least squares (Narasimhan & Jordache 2000):
    min sum ((q_i - v_i) / s_i)^2   s.t.  A q = b
has the closed form  q = v + S A^T (A S A^T)^-1 (b - A v)  with S = diag(s_i^2).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class Balance:
    """sum of the listed wells' rates should equal ``total`` (meter reading minus known non-well flows)."""

    name: str
    wells: Sequence[str]
    total: float


def reconcile(
    estimates: Mapping[str, float], sigmas: Mapping[str, float], balances: Sequence[Balance]
) -> dict[str, float]:
    wells = sorted(w for w in estimates if estimates[w] > 0)
    usable = [b for b in balances if any(w in wells for w in b.wells)]
    if not wells or not usable:
        return dict(estimates)
    index = {w: i for i, w in enumerate(wells)}
    v = np.array([estimates[w] for w in wells])
    s2 = np.array([max(sigmas[w], 1e-6) ** 2 for w in wells])
    a = np.zeros((len(usable), len(wells)))
    for r, balance in enumerate(usable):
        for w in balance.wells:
            if w in index:
                a[r, index[w]] = 1.0
    b = np.array([balance.total for balance in usable])
    gain = (s2[:, None] * a.T) @ np.linalg.pinv(a @ (s2[:, None] * a.T))
    q = np.maximum(v + gain @ (b - a @ v), 0.0)
    return {**estimates, **{w: float(q[index[w]]) for w in wells}}
