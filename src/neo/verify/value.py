"""Value a simulated outcome with the same commercial rules the optimiser uses.

Our sales energy is allocated to GSAs in priority order up to nominations, then to
excess outlets by price. Anything left cannot be sold and, with zero routine flaring,
is a violation of the plan, not a value.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from neo.domain.registry import Registry
from neo.verify.forward import Outcome


@dataclass(frozen=True, slots=True)
class Valuation:
    parts: Mapping[str, float]
    delivered: Mapping[str, float]
    excess: Mapping[str, float]
    shortfall: Mapping[str, float]
    unsold_mmbtu: float

    @property
    def value(self) -> float:
        return sum(self.parts.values())

    @property
    def shortfall_total(self) -> float:
        return sum(self.shortfall.values())


def reference_price(registry: Registry) -> float:
    gsas = registry.commercial.gsas
    return sum(c.price_usd_mmbtu * c.dcq_mmbtu_d for c in gsas) / sum(c.dcq_mmbtu_d for c in gsas)


def value_outcome(
    registry: Registry,
    outcome: Outcome,
    nominations: Mapping[str, float],
    *,
    well_ghv: Mapping[str, float] | None = None,
) -> Valuation:
    net, commercial, psc = registry.network, registry.commercial, registry.psc
    remaining = outcome.our_energy
    delivered, shortfall = {}, {}
    for contract in sorted(commercial.gsas, key=lambda c: c.priority):
        nominated = nominations.get(contract.id, 0.0)
        target = min(nominated, contract.mdq_mmbtu_d or nominated)
        delivered[contract.id] = min(target, remaining)
        shortfall[contract.id] = nominated - delivered[contract.id]
        remaining -= delivered[contract.id]
    excess = {}
    for outlet in sorted(commercial.excess_outlets, key=lambda o: -o.price_usd_mmbtu):
        excess[outlet.id] = min(outlet.cap_mmbtu_d, remaining)
        remaining -= excess[outlet.id]

    kept = 1 - net.plant.shrink_frac
    p_ref = reference_price(registry)
    rates = outcome.setpoints.rates if outcome.wells is None else {w: o.rate for w, o in outcome.wells.items()}
    ghv = well_ghv or {w.id: w.composition.ghv_btu_scf for w in net.wells}
    netback = {w.id: psc.by_id[w.psc].netback_factor for w in net.wells}
    parts = {
        "gas_sales": sum(delivered[c.id] * c.price_usd_mmbtu for c in commercial.gsas)
        + sum(excess[o.id] * o.price_usd_mmbtu for o in commercial.excess_outlets),
        "partner_share": -sum((1 - netback[w]) * p_ref * kept * ghv[w] * q for w, q in rates.items()),
        "condensate": sum(
            commercial.condensate_usd_bbl * netback[w] * bbl for w, bbl in outcome.our_condensate_by_well.items()
        ),
        "opex": -sum(net.platform_by_id[net.well_by_id[w].platform].opex_usd_per_mmscf * q for w, q in rates.items()),
    }
    return Valuation(parts, delivered, excess, shortfall, max(remaining, 0.0))
