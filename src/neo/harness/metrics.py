"""Daily records and the paired comparison of the advisor against baselines."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np

POLICIES = ("advisor", "hold", "greedy")


@dataclass(frozen=True, slots=True)
class PolicyDay:
    value_usd: float
    shortfall_mmbtu: float
    hard_violations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DayRecord:
    scenario: str
    gas_day: date
    advisory_status: str
    advise_seconds: float
    policies: dict[str, PolicyDay]

    def row(self) -> dict:
        out = {
            "scenario": self.scenario,
            "gas_day": self.gas_day.isoformat(),
            "advisory_status": self.advisory_status,
            "advise_seconds": round(self.advise_seconds, 3),
        }
        for name, p in self.policies.items():
            out[f"{name}_value_usd"] = round(p.value_usd, 0)
            out[f"{name}_shortfall_mmbtu"] = round(p.shortfall_mmbtu, 0)
            out[f"{name}_violations"] = ";".join(p.hard_violations)
        return out


@dataclass(frozen=True, slots=True)
class Uplift:
    baseline: str
    mean_usd_d: float
    ci90: tuple[float, float]
    mean_pct: float
    days_better: int
    days: int


def paired_uplift(records: Sequence[DayRecord], baseline: str, *, seed: int = 0, resamples: int = 2000) -> Uplift:
    deltas = np.array([r.policies["advisor"].value_usd - r.policies[baseline].value_usd for r in records])
    bases = np.array([r.policies[baseline].value_usd for r in records])
    rng = np.random.default_rng(seed)
    boots = [rng.choice(deltas, size=len(deltas), replace=True).mean() for _ in range(resamples)]
    lo, hi = np.percentile(boots, [5, 95])
    return Uplift(
        baseline=baseline,
        mean_usd_d=float(deltas.mean()),
        ci90=(float(lo), float(hi)),
        mean_pct=float(100 * deltas.sum() / bases.sum()) if bases.sum() else 0.0,
        days_better=int((deltas > 0).sum()),
        days=len(deltas),
    )


def violation_days(records: Sequence[DayRecord], policy: str) -> int:
    return sum(bool(r.policies[policy].hard_violations) for r in records)


def summary_markdown(by_scenario: dict[str, list[DayRecord]]) -> str:
    lines = [
        "| Scenario | Days | Blocked | Advisor violation-days | Hold violation-days | Greedy violation-days "
        "| Uplift vs hold (USD/d, 90% CI) | Uplift vs greedy (USD/d, 90% CI) | Median advise s |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for scenario, records in by_scenario.items():
        if not records:
            continue
        cells = [scenario, str(len(records)), str(sum(r.advisory_status == "blocked" for r in records))]
        cells += [str(violation_days(records, p)) for p in POLICIES]
        for base in ("hold", "greedy"):
            u = paired_uplift(records, base)
            cells.append(f"{u.mean_usd_d:+,.0f} ({u.ci90[0]:+,.0f} to {u.ci90[1]:+,.0f}); {u.mean_pct:+.1f}%")
        cells.append(f"{np.median([r.advise_seconds for r in records]):.2f}")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"
