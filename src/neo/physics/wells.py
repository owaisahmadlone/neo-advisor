"""Gas-well deliverability.

Two representations, on purpose:

* ``BottomholeWell`` is the truth-side model: a backpressure IPR at the sandface
  (Rawlins & Schellhardt 1935) coupled to an average-temperature, average-z vertical
  lift relation  p_wf^2 = e^s p_wh^2 + F q^2  (Cullender-Smith simplified; Guo et al.
  2007 ch. 4). Solving the two together gives the rate a well delivers at a wellhead
  pressure.
* ``WellheadCurve`` is what the advisor uses: one lumped wellhead deliverability curve
  q = C (p_ws^2 - p_wh^2)^n, calibrated from well tests. The choke is treated as a free
  pressure absorber, so p_wh >= p_manifold (Kosmidis et al. 2005).

The structural gap between the two is deliberate: it is the plant-model mismatch a
deployed advisor faces.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np
from scipy.optimize import brentq

from neo.core.units import bara_to_psia, celsius_to_rankine


@dataclass(frozen=True, slots=True)
class WellheadCurve:
    """q = c * (p_ws^2 - p_wh^2)^n with q in MMscfd and pressures in bara."""

    c: float
    n: float
    p_ws: float

    @property
    def u_ws(self) -> float:
        return self.p_ws**2

    def rate_u(self, u_wh: float) -> float:
        """Rate at a squared wellhead pressure (bara^2)."""
        drawdown = self.u_ws - u_wh
        return self.c * drawdown**self.n if drawdown > 0 else 0.0

    def rate(self, p_wh: float) -> float:
        return self.rate_u(p_wh**2)

    def wellhead_pressure(self, q: float) -> float:
        """Flowing wellhead pressure that delivers ``q``; NaN if beyond the open-flow rate."""
        if q <= 0:
            return self.p_ws
        drawdown = (q / self.c) ** (1 / self.n)
        return math.sqrt(self.u_ws - drawdown) if drawdown < self.u_ws else math.nan

    def scaled(self, factor: float) -> WellheadCurve:
        return replace(self, c=self.c * factor)

    @classmethod
    def from_test(cls, *, q: float, p_wh: float, p_ws: float, n: float) -> WellheadCurve:
        drawdown = p_ws**2 - p_wh**2
        if drawdown <= 0 or q <= 0:
            raise ValueError(f"test point q={q}, p_wh={p_wh} is inconsistent with p_ws={p_ws}")
        return cls(c=q / drawdown**n, n=n, p_ws=p_ws)


@dataclass(frozen=True, slots=True)
class BottomholeWell:
    """Backpressure IPR + average-T,z lift. Truth-side model."""

    c_r: float
    n_r: float
    p_res: float
    column_s: float
    friction_f: float

    @property
    def shut_in_wellhead_pressure(self) -> float:
        return self.p_res * math.exp(-self.column_s / 2)

    def _residual(self, q: float, p_wh: float) -> float:
        p_wf_sq = math.exp(self.column_s) * p_wh**2 + self.friction_f * q**2
        drawdown = self.p_res**2 - p_wf_sq
        inflow = self.c_r * drawdown**self.n_r if drawdown > 0 else 0.0
        return inflow - q

    def rate_at_wellhead(self, p_wh: float) -> float:
        if p_wh >= self.shut_in_wellhead_pressure:
            return 0.0
        q_hi = self.c_r * self.p_res ** (2 * self.n_r)
        return brentq(self._residual, 0.0, q_hi, args=(p_wh,), xtol=1e-9)

    def fit_wellhead_curve(self, *, n: float | None = None, points: int = 25) -> WellheadCurve:
        """Least-squares wellhead curve over the flowing range (what an engineer fits offline)."""
        p_ws = self.shut_in_wellhead_pressure
        p_wh = np.linspace(0.25 * p_ws, 0.95 * p_ws, points)
        q = np.array([self.rate_at_wellhead(p) for p in p_wh])
        x = np.log(p_ws**2 - p_wh**2)
        y = np.log(q)
        if n is None:
            n_fit, log_c = np.polyfit(x, y, 1)
        else:
            n_fit, log_c = n, float(np.mean(y - n * x))
        return WellheadCurve(c=float(math.exp(log_c)), n=float(n_fit), p_ws=p_ws)


def turner_min_rate(
    p_bara: float,
    t_c: float,
    tubing_id_in: float,
    *,
    z: float = 0.9,
    gas_sg: float = 0.65,
    liquid: str = "condensate",
) -> float:
    """Critical rate below which a gas well loads up with liquid (Turner et al. 1969).

    Field-unit form with Turner's 20% upward adjustment; returns MMscfd.
    """
    sigma, rho_l = (20.0, 45.0) if liquid == "condensate" else (60.0, 67.0)  # dyn/cm, lb/ft3
    p_psia = bara_to_psia(p_bara)
    t_r = celsius_to_rankine(t_c)
    rho_g = 2.715 * gas_sg * p_psia / (z * t_r)
    v_t = 1.912 * sigma**0.25 * (rho_l - rho_g) ** 0.25 / rho_g**0.5
    area_ft2 = math.pi / 4 * (tubing_id_in / 12) ** 2
    return 3.067 * p_psia * v_t * area_ft2 / (t_r * z)
