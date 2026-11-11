"""Page 4: run an advisory (what-if lab or real landing-zone data) and explain every output."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from neo.advise import AdviceBundle, AdviseConfig, advise
from neo.domain.fixtures import FixtureSpec, state_from_registry
from neo.domain.state import NetworkState
from neo.explain.text import render_report, well_sentence
from neo.ingest.landing import LandingZone
from neo.ingest.state_builder import build_state
from neo.opt.pwl import conservative_planes
from neo.ui import charts
from neo.ui.common import (
    available_gas_days,
    code_meaning,
    column_help,
    explain,
    fixtures,
    money,
    page_header,
    registry,
    zone_selector,
)
from neo.ui.netmap import MapData, network_figure

MAX_RUNS = 2


# ------------------------------------------------------------------ inputs
def _what_if_state() -> tuple[NetworkState, str] | None:
    reg = registry()
    net = reg.network
    presets = fixtures()
    preset = st.selectbox(
        "Start from a preset",
        list(presets),
        index=list(presets).index("normal") if "normal" in presets else 0,
        help="normal: everything available · trip: one compressor train down · co2cut: CO₂ removal at 60% · low_demand: buyers nominate less",
    )
    base = FixtureSpec.load(presets[preset])
    st.caption("Then change any condition below. Each control says what it represents in the real plant.")
    c1, c2, c3 = st.columns(3)
    trains = c1.slider(
        "Compressor trains available",
        0,
        net.compressor.trains,
        base.trains_available if base.trains_available is not None else net.compressor.trains,
        help="A trip or maintenance takes trains out; fewer trains = less compression.",
    )
    co2 = c1.slider(
        "CO₂ removal capacity (MMscfd)",
        0.0,
        30.0,
        float(
            base.co2_removal_cap_mmscfd if base.co2_removal_cap_mmscfd is not None else net.plant.co2_removal_cap_mmscfd
        ),
        0.5,
        help="How much CO₂ the plant can strip per day. Lower = high-CO₂ wells must be choked.",
    )
    inlet = c1.slider(
        "Plant inlet capacity (MMscfd)",
        200.0,
        600.0,
        float(base.plant_inlet_cap_mmscfd or net.plant.inlet_cap_mmscfd),
        10.0,
    )
    b1 = c2.slider(
        "Buyer 1 nomination (% of DCQ)",
        0,
        120,
        int(100 * base.nomination_fraction.get("GSA-B1", 1.0)),
        help="What the priority-1 buyer asks for tomorrow.",
    )
    b2 = c2.slider(
        "Buyer 2 nomination (% of DCQ)",
        0,
        120,
        int(100 * base.nomination_fraction.get("GSA-B2", 1.0)),
        help="What the priority-2 buyer asks for tomorrow.",
    )
    pac = c2.slider(
        "Third-party gas through the hub (MMscfd)",
        0.0,
        80.0,
        float(
            base.third_party_rate_mmscfd
            if base.third_party_rate_mmscfd is not None
            else net.third_party[0].nominal_rate_mmscfd
        ),
        1.0,
        help="Shares the compressor, trunks and plant.",
    )
    ambient = c3.slider(
        "Ambient temperature (°C)",
        24.0,
        38.0,
        float(base.ambient_c),
        0.5,
        help="Hot days derate the compressor turbines.",
    )
    headroom = c3.slider(
        "Condensate terminal headroom (bbl/d)", 5_000.0, 30_000.0, float(base.condensate_headroom_bbld), 500.0
    )
    current = c3.slider(
        "Today's rates (% of design)",
        50,
        110,
        int(100 * base.current_rate_fraction),
        help="Where the wells are now; step limits are measured from here.",
    )
    unavailable = st.multiselect(
        "Wells out of service (failure, workover)", [w.id for w in net.wells], default=base.wells_unavailable
    )
    spec = FixtureSpec(
        gas_day=base.gas_day,
        trains_available=trains,
        trains_running=min(base.trains_running if base.trains_running is not None else trains, trains),
        co2_removal_cap_mmscfd=co2,
        plant_inlet_cap_mmscfd=inlet,
        nomination_fraction={"GSA-B1": b1 / 100, "GSA-B2": b2 / 100},
        current_rate_fraction=current / 100,
        ambient_c=ambient,
        third_party_rate_mmscfd=pac,
        condensate_headroom_bbld=headroom,
        wells_unavailable=unavailable,
    )
    label = f"what-if from '{preset}'"
    return state_from_registry(reg, spec), label


def _landing_state() -> tuple[NetworkState, str] | None:
    zone_dir = zone_selector()
    if zone_dir is None:
        return None
    days = available_gas_days(zone_dir)
    if not days:
        st.warning("This landing zone does not cover a full cut-off window yet.")
        return None
    gas_day = st.select_slider("Gas day", days, value=days[min(3, len(days) - 1)])
    state = _cached_state(str(zone_dir), gas_day)
    return state, f"{Path(zone_dir).parent.name} · {gas_day}"


@st.cache_resource(show_spinner="Ingesting and calibrating…", max_entries=8)
def _cached_state(zone_dir: str, gas_day) -> NetworkState:
    return build_state(LandingZone(Path(zone_dir)), gas_day)


# ----------------------------------------------------------------- outputs
def _summary(bundle: AdviceBundle) -> None:
    a = bundle.advisory
    if a.status == "blocked":
        st.error("**Blocked: no advisory issued.** Operators keep yesterday's settings until the problem is fixed.")
    elif a.status == "ready_with_flags":
        st.warning("**Ready, with flags.** The advice is valid; read the flags below before approving.")
    else:
        st.success("**Ready.** The advice is valid and nothing needs special attention.")
    st.caption(code_meaning("advisory_status", a.status))
    if a.status_reasons:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "code": r.code,
                        "subject": r.subject,
                        "message": r.message,
                        "what it means": code_meaning("status_reason", r.code),
                    }
                    for r in a.status_reasons
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
    if a.status == "blocked":
        return
    plan = sum(v.plan_usd_d for v in a.value_waterfall)
    delta = sum(v.delta_usd_d for v in a.value_waterfall)
    short = sum(c.shortfall_mmbtu_d for c in a.allocations)
    m = st.columns(5)
    m[0].metric(
        "Plan value (USD/d)",
        f"${plan / 1e6:.2f}M",
        money(delta, signed=True) + " vs holding",
        help="Enterprise value of the plan: gas sales + condensate − partners' share − operating cost.",
    )
    if a.plan_value_usd_d:
        m[1].metric(
            "P10–P90 (USD/d)",
            f"{a.plan_value_usd_d.p10_usd_d / 1e6:.2f}–{a.plan_value_usd_d.p90_usd_d / 1e6:.2f}M",
            help="Range if wells deliver more or less than calibrated (Monte Carlo on well uncertainty).",
        )
    m[2].metric(
        "Shortfall (MMBtu/d)",
        f"{short / 1000:,.1f}k" if short >= 1000 else f"{short:,.0f}",
        help="Gas buyers asked for that the network cannot deliver today.",
    )
    m[3].metric(
        "Gas value at margin ($/MMBtu)",
        f"${a.marginal_gas_value_usd_mmbtu:.2f}",
        help="What one more MMBtu of sales gas would earn today: the price of the last buyer served.",
    )
    m[4].metric(
        "Hub suction (bara)", f"{a.network.suction_setpoint_bara:.1f}", f"{a.network.trains_online} trains online"
    )
    moves = [w for w in a.wells if w.action not in ("maintain", "unavailable")]
    top = ", ".join(b.label for b in a.binding_constraints[:3]) or "none"
    st.info(
        f"**In one sentence:** move {len(moves)} wells, set hub suction to {a.network.suction_setpoint_bara:.1f} bara "
        f"with {a.network.trains_online} trains, deliver {sum(c.planned_mmbtu_d for c in a.allocations):,.0f} MMBtu/d "
        f"to buyers; today's network is held back mostly by: {top}."
    )


def _map(bundle: AdviceBundle, state: NetworkState) -> None:
    a, plan, out = bundle.advisory, bundle.plan, bundle.plan_outcome
    data = MapData(
        well_rates=dict(plan.rates),
        arc_flow=dict(out.arc_flow),
        pressures=dict(out.pressures),
        routes=dict(plan.routes),
        hub_inflow=out.hub_inflow,
        plant_inlet=out.plant_inlet,
        third_party=dict(state.third_party_rates),
        deliveries={
            **{c.contract_id: c.planned_mmbtu_d for c in a.allocations},
            **{e.outlet_id: e.planned_mmbtu_d for e in a.excess},
        },
        condensate_bbld=out.condensate,
        trains=plan.trains,
    )
    st.plotly_chart(network_figure(registry(), data), use_container_width=True)
    st.caption(
        "Line width = planned flow. Hover for pressures (from the exact-physics re-check) and flows. Dashed = route not used."
    )


def _wells(bundle: AdviceBundle) -> None:
    a = bundle.advisory
    rows = [
        {
            "rank": w.rank,
            "well_id": w.well_id,
            "action": w.action,
            "current_rate_mmscfd": w.current_rate_mmscfd,
            "recommended_rate_mmscfd": w.recommended_rate_mmscfd,
            "implied_choke_pct": w.implied_choke_pct,
            "unit_value_usd_per_mmscfd": w.unit_value_usd_per_mmscfd,
            "total_contribution_usd_d": w.total_contribution_usd_d,
            "merit_status": w.merit_status,
            "confidence": w.confidence,
            "flags": ", ".join(w.flags),
        }
        for w in sorted(a.wells, key=lambda w: (w.rank is None, w.rank or 0))
    ]
    df = pd.DataFrame(rows)
    st.dataframe(
        df, column_config=column_help("OUTPUTS", "WellAdvice", df.columns), use_container_width=True, hide_index=True
    )
    used = sorted(
        {w.action for w in a.wells} | {f for w in a.wells for f in w.flags} | {w.merit_status for w in a.wells}
    )
    with st.expander("ⓘ What the actions, statuses and flags in this table mean", expanded=False):
        for code in used:
            category = (
                "action"
                if code in {w.action for w in a.wells}
                else ("merit_status" if code in {w.merit_status for w in a.wells} else "well_flag")
            )
            st.markdown(f"- **{code}**: {code_meaning(category, code)}")
    if a.choke_back_order:
        st.markdown(
            "**If a limit tightens during the day** (e.g. CO₂ creeps up), operators choke back in this order, lowest "
            "value first: " + " → ".join(a.choke_back_order)
        )


def _merit(bundle: AdviceBundle) -> None:
    st.plotly_chart(charts.merit_breakdown(bundle.merit), use_container_width=True)
    explain(
        "How to read the merit order",
        "Each bar is one well. Coloured segments are what **one more MMscfd** from that well is worth to the "
        "**whole network**: gas sold at today's marginal price and condensate earn money (right); the partners' "
        "and government share, operating cost and the limits it consumes cost money (left). That includes CO₂ "
        "load on the plant, back-pressure on other wells sharing its line, and compression. The black diamond is "
        "the net.\n\n"
        "- **at_max** (net > 0): the network wants all it can give, so open fully.\n"
        "- **swing** (net ≈ 0): this well absorbs the binding limit; its rate is what's being tuned.\n"
        "- **at_min / negative**: costs more than it earns; first to choke or shut.\n"
        "- **off**: ranked by the value of switching it on (a re-solve), because opening a well jumps to its "
        "minimum stable rate.\n\n"
        "The numbers come from the optimiser's shadow prices (LP duals) and are cross-checked by re-solving with "
        "slightly more deliverability.",
        expanded=True,
    )
    a = bundle.advisory
    ranked = sorted((w for w in a.wells if w.rank is not None), key=lambda w: w.rank)
    with st.expander("Every well in words"):
        for w in ranked:
            st.markdown(f"{w.rank}. {well_sentence(w, len(ranked))}")


def _binding(bundle: AdviceBundle) -> None:
    a = bundle.advisory
    if not a.binding_constraints:
        st.markdown("No network-wide limit binds: the wells themselves are the limit.")
        return
    df = pd.DataFrame([b.model_dump() for b in a.binding_constraints])
    df["what it means"] = [code_meaning("row_kind", b.key.split(":")[0]) for b in a.binding_constraints]
    st.dataframe(
        df,
        column_config=column_help("OUTPUTS", "BindingConstraint", df.columns),
        use_container_width=True,
        hide_index=True,
    )
    explain(
        "Shadow values",
        "A binding limit is one the plan presses against. Its **shadow value** is how much the day's value would "
        "rise if that limit were relaxed by one unit (one MMscfd of CO₂ removal, one bar of pressure…). It tells "
        "management where debottlenecking pays: a large shadow value on CO₂ removal means more removal capacity "
        "is worth that much per day.",
    )


def _value(bundle: AdviceBundle) -> None:
    a = bundle.advisory
    st.plotly_chart(charts.value_waterfall(a), use_container_width=True)
    df = pd.DataFrame([v.model_dump() for v in a.value_waterfall])
    df["what it is"] = [code_meaning("value_component", v.component) for v in a.value_waterfall]
    st.dataframe(
        df, column_config=column_help("OUTPUTS", "ValueLine", df.columns), use_container_width=True, hide_index=True
    )
    explain(
        "What 'holding current settings' means",
        "The comparison point is what the plant would actually do today if nobody changed anything: wells clipped "
        "to what they can deliver, and suction floating up if the compressors can't keep up. It is simulated with "
        "the same physics. On a trip day, holding often breaches a limit; the status flags say so.",
    )


def _contracts(bundle: AdviceBundle) -> None:
    a = bundle.advisory
    c1, c2 = st.columns(2)
    c1.plotly_chart(charts.contracts(a), use_container_width=True)
    c2.plotly_chart(charts.sales_quality(a, registry().network.plant.spec), use_container_width=True)
    df = pd.DataFrame([c.model_dump() for c in a.allocations])
    st.dataframe(
        df,
        column_config=column_help("OUTPUTS", "ContractAllocation", df.columns),
        use_container_width=True,
        hide_index=True,
    )
    st.markdown("**Draft nomination** (for the planner; never submitted automatically):")
    st.dataframe(pd.DataFrame(bundle.nomination_draft), use_container_width=True, hide_index=True)


def _verification(bundle: AdviceBundle) -> None:
    out = bundle.plan_outcome
    st.plotly_chart(charts.check_margins([c for c in out.checks if c.limit != "move_limit"]), use_container_width=True)
    checks = pd.DataFrame(
        [
            {
                "limit": c.limit,
                "subject": c.subject,
                "value": c.value,
                "bound": c.bound,
                "kind": "max" if c.upper else "min",
                "margin": c.margin,
                "held": c.ok,
                "severity": c.severity,
                "what it means": code_meaning("limit", c.limit),
            }
            for c in out.checks
        ]
    )
    st.dataframe(checks, use_container_width=True, hide_index=True)
    attempts = bundle.manifest.get("solve_attempts", [])
    st.markdown(
        f"**Verification attempts:** {len(attempts)}. If a limit fails, NeO tightens its safety margins and re-solves (up to 3 attempts); if it still fails, the advisory is blocked."
    )
    st.json(attempts, expanded=False)
    explain(
        "Why a second check",
        "The optimiser works on straight-line approximations. The verifier recomputes every pressure, flow, "
        "blend and liquid volume with the exact nonlinear equations, working back from the pressure set-points, "
        "and checks each hard limit in plain code. Because the approximations are conservative, a failure here "
        "means a bug, not bad luck. Real-world mismatch is handled by safety margins and measured in the "
        "evaluation (page 5).",
    )


def _compressor(bundle: AdviceBundle, state: NetworkState) -> None:
    reg = registry()
    comp = reg.network.compressor
    hub = reg.network.node_by_id[comp.suction_node]
    train = comp.train.map
    ps, pd_ = bundle.plan.suction_bara, bundle.plan_outcome.pressures[comp.discharge_node]
    planes = conservative_planes(
        lambda s, d: train.capacity_mmscfd(s, d, state.ambient_c),
        ps_range=(hub.p_min_bara, hub.p_max_bara),
        pd_range=(reg.network.arrival_node.setpoint_bara, comp.pd_max_bara),
        ratio_max=train.ratio_max,
    )
    st.plotly_chart(
        charts.compressor_map(
            train,
            ambient_c=state.ambient_c,
            ps_range=(hub.p_min_bara, hub.p_max_bara),
            pd_range=(reg.network.arrival_node.setpoint_bara, comp.pd_max_bara),
            planes=planes,
            point=(ps, pd_),
        ),
        use_container_width=True,
    )
    n = bundle.advisory.network
    st.markdown(
        f"Plan: **{n.trains_online} of {n.trains_available}** trains at suction **{ps:.1f} bara**, discharge {pd_:.1f} bara "
        f"(ratio {pd_ / ps:.2f}); hub inflow {n.hub_inflow_mmscfd:.0f} MMscfd, headroom {n.compression_ullage_mmscfd:.0f} MMscfd. "
        "Each train carries inflow ÷ trains; the white dot must sit in the coloured region with enough capacity."
    )


def _internals(bundle: AdviceBundle) -> None:
    sol = bundle.solution
    if sol is None:
        return
    rows = [
        {
            "stage": k,
            "status": s.solution.status,
            "objective": round(s.solution.objective, 2),
            "rows": len(s.pm.model.rows),
            "columns": len(s.pm.model.columns),
            "integer": s.pm.model.is_mip,
            "seconds": round(s.seconds, 4),
            "what it does": code_meaning("stage", k),
        }
        for k, s in sol.stages.items()
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.json(bundle.manifest, expanded=False)


def _artefacts(bundle: AdviceBundle) -> None:
    a = bundle.advisory
    payload = a.model_dump_json(indent=2)
    report = render_report(a)
    c = st.columns(3)
    c[0].download_button("advisory.json", payload, file_name="advisory.json", mime="application/json")
    c[1].download_button("advisory_report.md", report, file_name="advisory_report.md")
    c[2].download_button(
        "run_manifest.json (internal)",
        json.dumps(bundle.manifest, indent=2, default=str),
        file_name="run_manifest.json",
    )
    t1, t2 = st.tabs(["Planner report (as published)", "advisory.json (client-visible contract)"])
    t1.markdown(report)
    t2.json(json.loads(payload), expanded=False)


def _compare(runs: list[tuple[str, AdviceBundle]]) -> None:
    (label_a, a), (label_b, b) = runs[-2], runs[-1]
    if a.advisory.status == "blocked" or b.advisory.status == "blocked":
        return
    st.subheader("What changed since the previous run")
    prev = {w.well_id: w for w in a.advisory.wells}
    rows = [
        {
            "well": w.well_id,
            f"before ({label_a})": prev[w.well_id].recommended_rate_mmscfd,
            f"now ({label_b})": w.recommended_rate_mmscfd,
            "change": round(w.recommended_rate_mmscfd - prev[w.well_id].recommended_rate_mmscfd, 2),
            "rank before": prev[w.well_id].rank,
            "rank now": w.rank,
        }
        for w in b.advisory.wells
    ]
    df = pd.DataFrame(rows)
    st.dataframe(df.loc[df["change"].abs() > 0.05], use_container_width=True, hide_index=True)
    va = sum(v.plan_usd_d for v in a.advisory.value_waterfall)
    vb = sum(v.plan_usd_d for v in b.advisory.value_waterfall)
    st.markdown(
        f"Value {money(va)} → {money(vb)} ({money(vb - va, signed=True)}/d). Binding limits before: "
        f"{', '.join(x.label for x in a.advisory.binding_constraints[:3]) or 'none'}; now: "
        f"{', '.join(x.label for x in b.advisory.binding_constraints[:3]) or 'none'}."
    )


def render() -> None:
    page_header(
        "4 · Run an advisory",
        "Produce tomorrow's advice and see every part of it: set-points per well, the network settings, the "
        "merit order with reasons, binding limits, value against holding, contracts, sales-gas quality, the "
        "exact-physics re-check and the published artefacts.",
        "This is the product. Change the conditions in the what-if lab and watch how the advice and its reasons "
        "respond; that is the fastest way to build intuition for (and to challenge) the method.",
    )
    source = st.radio(
        "Inputs", ["What-if lab (registry-based, instant)", "Landing-zone data (full ingest)"], horizontal=True
    )
    with st.container(border=True):
        built = _what_if_state() if source.startswith("What-if") else _landing_state()
    if built is None:
        return
    state, label = built
    samples = st.slider(
        "Monte Carlo samples for the value band", 0, 100, 30, 10, help="More samples = smoother P10/P90, slower run."
    )
    if st.button("Run NeO", type="primary"):
        with st.spinner("Gate → solve (3 priorities) → verify → explain…"):
            bundle = advise(registry(), state, cfg=AdviseConfig(value_samples=samples))
        runs = st.session_state.setdefault("runs", [])
        runs.append((label, bundle))
        del runs[:-MAX_RUNS]
        st.session_state["last_state"] = state
    runs = st.session_state.get("runs", [])
    if not runs:
        st.info("Set the conditions and press **Run NeO**.")
        return
    label, bundle = runs[-1]
    state = st.session_state.get("last_state", state)
    st.markdown(f"### Result: {label}")
    _summary(bundle)
    if bundle.advisory.status == "blocked":
        _artefacts(bundle)
        return
    tabs = st.tabs(
        [
            "Network",
            "Well set-points",
            "Merit order",
            "Binding limits",
            "Value",
            "Contracts & quality",
            "Compression",
            "Verification",
            "Under the hood",
            "Published artefacts",
        ]
    )
    with tabs[0]:
        _map(bundle, state)
    with tabs[1]:
        _wells(bundle)
    with tabs[2]:
        _merit(bundle)
    with tabs[3]:
        _binding(bundle)
    with tabs[4]:
        _value(bundle)
    with tabs[5]:
        _contracts(bundle)
    with tabs[6]:
        _compressor(bundle, state)
    with tabs[7]:
        _verification(bundle)
    with tabs[8]:
        _internals(bundle)
    with tabs[9]:
        _artefacts(bundle)
    if len(runs) >= 2:
        _compare(runs)
