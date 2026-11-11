"""Incumbent practices the advisor must beat, built from the advisor's own information.

* ``hold``: keep yesterday's set-points.
* ``greedy``: a planner's spreadsheet logic. Open wells in priority-of-supply order (then by
  test rate) until the nominations are covered or the hub is full, then choke the
  highest-CO2 wells until the CO2 load fits the removal capacity (Shell's incumbent at
  Sarawak was an Excel CO2 checker, SPE 145723).
"""

from __future__ import annotations

from neo.advise import current_setpoints
from neo.domain.registry import Registry
from neo.domain.state import NetworkState
from neo.physics.blend import Composition, blend, co2_removal_needed
from neo.verify.forward import Setpoints

CHOKE_STEP = 0.9


def hold(state: NetworkState) -> Setpoints:
    return current_setpoints(state)


def greedy(registry: Registry, state: NetworkState) -> Setpoints:
    net, psc = registry.network, registry.psc
    comp = net.compressor
    current = current_setpoints(state)
    target_energy = sum(state.nominations_mmbtu_d.values()) / (1 - net.plant.shrink_frac)
    hub_capacity = current.trains * comp.train.map.capacity_mmscfd(current.suction_bara, 75.0, state.ambient_c)
    third_party = sum(state.third_party_rates.values())

    def manifold_guess(platform: str) -> float:
        route = current.routes.get(platform, net.platform_by_id[platform].export_options[0])
        return current.suction_bara + 12.0 if net.pipe_by_id[route].to_node == comp.suction_node else 80.0

    order = sorted(
        (w for w in net.wells if state.wells[w.id].available),
        key=lambda w: (psc.by_id[w.psc].pos_rank, -state.wells[w.id].curve.rate(manifold_guess(w.platform))),
    )
    rates = {w.id: 0.0 for w in net.wells}
    energy, hub = 0.0, third_party
    for well in order:
        if energy >= target_energy:
            break
        q = min(well.rate_cap_mmscfd, state.wells[well.id].curve.rate(manifold_guess(well.platform)))
        to_hub = net.pipe_by_id[current.routes[well.platform]].to_node == comp.suction_node
        if to_hub:
            q = min(q, max(hub_capacity - hub, 0.0))
        if q < well.min_rate_mmscfd:
            continue
        rates[well.id] = q
        energy += q * well.composition.ghv_btu_scf
        hub += q if to_hub else 0.0

    by_co2 = sorted((w for w in net.wells if rates[w.id] > 0), key=lambda w: -w.co2)
    for _ in range(200):
        streams: list[tuple[float, Composition]] = [(rates[w.id], w.composition) for w in net.wells]
        streams += [(state.third_party_rates[s.id], s.composition) for s in net.third_party]
        q_in, mix = blend(streams)
        needed = co2_removal_needed(
            q_in=q_in, inlet=mix, co2_max=net.plant.spec.co2_max, shrink_frac=net.plant.shrink_frac
        )
        if needed <= state.co2_removal_cap_mmscfd * 0.98 or not by_co2:
            break
        worst = by_co2[0]
        rates[worst.id] *= CHOKE_STEP
        if rates[worst.id] < worst.min_rate_mmscfd:
            rates[worst.id] = 0.0
            by_co2.pop(0)
    return Setpoints(rates, current.routes, current.trains, current.suction_bara)
