"""Planner-facing report: the advisory in sentences, moves in a safe order."""

from __future__ import annotations

from neo.domain.advisory import Advisory, WellAdvice

ACTION_ORDER = {"shut_in": 0, "decrease": 1, "maintain": 2, "open_fully": 3, "increase": 4, "open": 5, "unavailable": 6}


def _money(value: float) -> str:
    sign = "-" if value < 0 else "+"
    return f"{sign}${abs(value):,.0f}"


def _row(*cells: object) -> str:
    return "| " + " | ".join(str(c) for c in cells) + " |"


def well_sentence(w: WellAdvice, total: int) -> str:
    if w.rank is None:
        return f"{w.well_id} is unavailable."
    head = f"{w.well_id} ranks {w.rank} of {total}"
    if w.unit_value_usd_per_mmscfd is not None:
        head += f" ({_money(w.unit_value_usd_per_mmscfd)}/d per extra MMscfd)"
    parts = [f"{_money(r.usd_per_mmscfd)} {r.label}" for r in w.reasons[:4]]
    return f"{head}: " + ", ".join(parts) + "." if parts else f"{head}."


def render_report(a: Advisory) -> str:
    lines = [
        f"# NeO advisory — gas day {a.gas_day.isoformat()} (revision {a.revision})",
        "",
        f"Status: **{a.status}** · run `{a.run_id}` · data cut-off {a.snapshot_cutoff_utc:%Y-%m-%d %H:%M} UTC",
        "",
    ]
    if a.status == "blocked":
        lines += ["## Why no advisory was issued", ""]
        lines += [f"- {r.code}: {r.message}" + (f" ({r.subject})" if r.subject else "") for r in a.status_reasons]
        return "\n".join(lines) + "\n"

    delta = sum(v.delta_usd_d for v in a.value_waterfall)
    plan = sum(v.plan_usd_d for v in a.value_waterfall)
    lines += [
        "## Summary",
        "",
        f"- Plan value {plan:,.0f} USD/d, {_money(delta)}/d against holding current settings.",
    ]
    if a.plan_value_usd_d:
        r = a.plan_value_usd_d
        band = f"P10 {r.p10_usd_d:,.0f} · P50 {r.p50_usd_d:,.0f} · P90 {r.p90_usd_d:,.0f}"
        lines.append(f"- Range given well-rate uncertainty: {band} USD/d.")
    if a.marginal_gas_value_usd_mmbtu is not None:
        lines.append(f"- One more MMBtu of sales gas is worth {a.marginal_gas_value_usd_mmbtu:.2f} USD today.")
    if a.binding_constraints:
        top = ", ".join(b.label for b in a.binding_constraints[:3])
        lines.append(f"- Limits that bind today: {top}.")
    lines += [f"- {r.code}: {r.message}" + (f" ({r.subject})" if r.subject else "") for r in a.status_reasons]

    moves = sorted(
        (w for w in a.wells if w.action not in ("maintain", "unavailable")),
        key=lambda w: (ACTION_ORDER[w.action], w.well_id),
    )
    lines += [
        "",
        "## Moves, in order (reductions first)",
        "",
        "| # | Well | Action | Now | Target | Choke % | Flags |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, w in enumerate(moves, start=1):
        choke = f"{w.implied_choke_pct:.0f}" if w.implied_choke_pct is not None else "—"
        now = f"{w.current_rate_mmscfd:.1f}"
        target = (
            f"open fully (~{w.recommended_rate_mmscfd:.1f})"
            if "open_fully" in w.flags
            else f"{w.recommended_rate_mmscfd:.1f}"
        )
        flags = ", ".join(f for f in w.flags if f != "open_fully")
        lines.append(_row(i, w.well_id, w.action, now, target, choke, flags))
    if a.choke_back_order:
        lines += [
            "",
            "If a plant or network limit tightens during the day, choke back in this order: "
            + ", ".join(a.choke_back_order[:6])
            + ".",
        ]
    if a.network:
        n = a.network
        lines += [
            "",
            "## Network set-points",
            "",
            f"- Hub suction set-point {n.suction_setpoint_bara:.1f} bara, "
            f"{n.trains_online} of {n.trains_available} trains online, inflow {n.hub_inflow_mmscfd:.0f} MMscfd, "
            f"compression headroom {n.compression_ullage_mmscfd:.0f} MMscfd.",
            "- Routing: " + ", ".join(f"{p} → {pipe}" for p, pipe in sorted(n.routes.items())) + ".",
        ]
    if a.sales_gas:
        s = a.sales_gas
        lines.append(
            f"- Sales gas {s.volume_mmscfd:.0f} MMscfd at {s.ghv_btu_scf:.0f} Btu/scf and "
            f"{s.co2_mol_pct:.1f} mol% CO2 after removing {s.co2_removal_mmscfd:.1f} MMscfd of CO2."
        )
    lines += ["", "## Contracts", "", "| Contract | Buyer | Nominated | Planned | Shortfall |", "|---|---|---|---|---|"]
    lines += [
        _row(
            c.contract_id,
            c.buyer,
            f"{c.nominated_mmbtu_d:,.0f}",
            f"{c.planned_mmbtu_d:,.0f}",
            f"{c.shortfall_mmbtu_d:,.0f}",
        )
        for c in a.allocations
    ]
    lines += [_row(e.outlet_id, e.buyer, "—", f"{e.planned_mmbtu_d:,.0f}", "—") for e in a.excess]
    lines += ["", "## Well merit order", ""]
    ranked = sorted((w for w in a.wells if w.rank is not None), key=lambda w: w.rank)
    lines += [f"{w.rank}. {well_sentence(w, len(ranked))}" for w in ranked]
    if a.data_flags:
        lines += ["", "## Data flags", ""]
        lines += [f"- {f.code}: {f.message} ({f.subject})" for f in a.data_flags]
    return "\n".join(lines) + "\n"
