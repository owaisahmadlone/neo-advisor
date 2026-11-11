"""Page 3: what the data gate accepted, and what calibration learned from the data."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from neo.core.units import barg_to_bara
from neo.ingest import schema
from neo.ingest.gate import GateConfig
from neo.ingest.landing import LandingZone
from neo.ingest.state_builder import build_state_traced
from neo.ui import charts
from neo.ui.common import (
    available_gas_days,
    code_meaning,
    column_help,
    explain,
    meaning,
    page_header,
    registry,
    zone_selector,
)


@st.cache_resource(show_spinner="Reading extracts, gating and calibrating…", max_entries=8)
def traced_state(zone_dir: str, gas_day):
    return build_state_traced(LandingZone(Path(zone_dir)), gas_day)


def render() -> None:
    page_header(
        "3 · Data gate & calibration",
        "For one gas day: the snapshot NeO takes at the cut-off, the gate's verdict on every critical sensor, and "
        "what calibration learned. That means each well's deliverability refitted to its latest accepted test, "
        "pipe friction fitted from two weeks of history, and well rates reconciled to the hub meter.",
        "Bad data in means bad advice out. The gate refuses to advise on broken critical data rather than guess, "
        "and calibration is how the model keeps up with a plant that changes every day.",
    )
    zone_dir = zone_selector()
    if zone_dir is None:
        return
    days = available_gas_days(zone_dir)
    if not days:
        st.warning("This landing zone does not cover a full cut-off window yet.")
        return
    gas_day = st.select_slider("Gas day to plan", days, value=days[min(3, len(days) - 1)])
    state, trace = traced_state(str(zone_dir), gas_day)
    st.session_state["traced"] = (str(zone_dir), gas_day)
    reg = registry()
    cfg = GateConfig()

    st.subheader("Verdict")
    blocking = state.blocking_flags
    if blocking:
        st.error(f"**Blocked**: {len(blocking)} critical problem(s). NeO will not issue an advisory for {gas_day}.")
    else:
        st.success(
            f"**Passed.** Data at the cut-off ({trace.cutoff:%Y-%m-%d %H:%M} UTC) is good enough to advise on {gas_day}."
        )
    if state.flags:
        flags = pd.DataFrame(
            [
                {
                    "severity": f.severity,
                    "code": f.code,
                    "subject": f.subject,
                    "message": f.message,
                    "what it means": code_meaning("data_flag", f.code),
                }
                for f in state.flags
            ]
        )
        st.dataframe(flags, use_container_width=True, hide_index=True)
    explain(
        "The gate's rules",
        f"Only **Good** samples in the {cfg.window_hours:.0f} h before the cut-off count. A **critical** tag "
        f"blocks the run if it has no Good sample (*bad*), if its last Good sample is older than "
        f"{cfg.stale_hours:.0f} h (*stale*), or if it has repeated exactly the same value for "
        f"{cfg.stuck_samples * 15 / 60:.0f} h (*stuck*; on/off tags are exempt). Non-critical gaps are filled "
        "with a stated substitute and flagged.",
    )

    st.subheader("Critical sensors at the cut-off")
    zone = LandingZone(zone_dir)
    rows = []
    for tag in sorted(zone.critical_tags):
        r = trace.readings.get(tag)
        status = "ok"
        if r is None or r.mean is None:
            status = "bad"
        elif r.last_good_utc < trace.cutoff - pd.Timedelta(hours=cfg.stale_hours):
            status = "stale"
        elif r.stuck:
            status = "stuck"
        rows.append(
            {
                "tag": tag,
                "status": status,
                "mean (window)": None if r is None else r.mean,
                "Good / total samples": "0/0" if r is None else f"{r.good}/{r.total}",
                "last Good (UTC)": None
                if r is None or r.last_good_utc is None
                else r.last_good_utc.strftime("%m-%d %H:%M"),
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.subheader("What NeO concluded about the network")
    c = st.columns(4)
    c[0].metric(
        "Compressor trains available", state.trains_available, help=meaning("STATE", "NetworkState", "trains_available")
    )
    c[1].metric("Trains running now", state.current.trains_running)
    c[2].metric("Hub suction now", f"{state.current.suction_bara:.1f} bara")
    c[3].metric("Ambient", f"{state.ambient_c:.1f} °C", help="Hot days derate the compressor gas turbines.")
    c = st.columns(4)
    c[0].metric("CO₂ removal capacity", f"{state.co2_removal_cap_mmscfd:.1f} MMscfd")
    c[1].metric("Plant inlet capacity", f"{state.plant_inlet_cap_mmscfd:.0f} MMscfd")
    c[2].metric("Terminal headroom", f"{state.condensate_headroom_bbld:,.0f} bbl/d")
    c[3].metric("Third-party gas", " · ".join(f"{k} {v:.1f}" for k, v in state.third_party_rates.items()) + " MMscfd")
    noms = pd.DataFrame([{"contract": k, "nominated (MMBtu/d)": v} for k, v in state.nominations_mmbtu_d.items()])
    st.dataframe(noms, use_container_width=True, hide_index=True)
    st.markdown(
        "**Current routing** (inferred: there are no valve-position tags, so NeO checks which route's physics matches the measured manifold pressure):"
    )
    for platform, route in state.current.routes.items():
        gaps = trace.route_gaps.get(platform)
        detail = ", ".join(f"{pipe}: off by {gap:.2f} bar" for pipe, gap in gaps.items()) if gaps else "only one route"
        st.markdown(f"- {platform} → **{route}** ({detail})")
    if not trace.capacity_events.empty:
        st.markdown("**Events that change capacity for this gas day:**")
        st.dataframe(trace.capacity_events, use_container_width=True, hide_index=True)

    st.subheader("Well calibration and reconciliation")
    wells = []
    for well in reg.network.wells:
        ws, cal = state.wells[well.id], trace.calibrations[well.id]
        wells.append(
            {
                "well": well.id,
                "online": ws.online,
                "available": ws.available,
                "raw rate estimate (VFM)": trace.raw_rates.get(well.id, 0.0),
                "reconciled rate": ws.rate_mmscfd,
                "test used": cal.used_test,
                "test age (days)": round(cal.test_age_days, 1),
                "uncertainty (σ)": f"{ws.sigma_rel:.1%}",
                "confidence": ws.confidence,
                "registry C": well.c_wh,
                "calibrated C": ws.curve.c,
                "shut-in p_ws (bara)": round(ws.curve.p_ws, 1),
            }
        )
    st.dataframe(
        pd.DataFrame(wells),
        column_config=column_help("STATE", "WellState", ["online", "available", "confidence"]),
        use_container_width=True,
        hide_index=True,
    )
    if trace.hub_meter is not None:
        raw = sum(trace.raw_rates.get(w, 0.0) for w in trace.hub_wells)
        target = trace.hub_meter - trace.third_party_total
        st.markdown(
            f"**Hub meter balance:** the meter reads {trace.hub_meter:.1f} MMscfd, of which {trace.third_party_total:.1f} is "
            f"third-party, so the hub-routed wells must sum to **{target:.1f}**. Their raw estimates sum to {raw:.1f}. "
            "Reconciliation spreads the difference in proportion to each estimate's uncertainty (weighted least "
            "squares), so the corrected rates match the meter exactly."
        )
        fig = go.Figure()
        fig.add_bar(x=trace.hub_wells, y=[trace.raw_rates[w] for w in trace.hub_wells], name="raw estimate")
        fig.add_bar(x=trace.hub_wells, y=[state.wells[w].rate_mmscfd for w in trace.hub_wells], name="reconciled")
        fig.update_layout(barmode="group", height=320, title="Rate estimates before and after reconciliation")
        st.plotly_chart(fig, use_container_width=True)
    well_id = st.selectbox("Well to inspect", [w.id for w in reg.network.wells])
    well = reg.network.well_by_id[well_id]
    tests = zone.well_tests
    accepted = tests.loc[(tests["well_id"] == well_id) & tests["accepted"] & (tests["test_start_utc"] <= trace.cutoff)]
    points = []
    if not accepted.empty:
        last = accepted.sort_values("test_start_utc").iloc[-1]
        points.append(
            (
                barg_to_bara(last["pwh_barg"]),
                last["q_gas_mmscfd"],
                f"latest accepted test ({last['test_start_utc']:%m-%d})",
            )
        )
    manifold = reg.network.platform_by_id[well.platform].manifold
    reading = trace.readings.get(schema.NETWORK_TAGS["manifold_pressure"].format(node=manifold))
    if reading is not None and reading.mean is not None:
        points.append(
            (barg_to_bara(reading.mean), state.wells[well_id].rate_mmscfd, "now (reconciled, at manifold pressure)")
        )
    st.plotly_chart(
        charts.well_curves(
            {well_id: {"registry curve": well.curve, "calibrated curve": state.wells[well_id].curve}},
            points={well_id: points},
        ),
        use_container_width=True,
    )
    explain(
        "How a well is calibrated",
        "The shut-in pressure p_ws comes from the last survey, declined linearly to today. The exponent n keeps "
        "its engineering value. The coefficient C is refitted so the curve passes exactly through the latest "
        "accepted test. Uncertainty starts at 3% and grows 0.1%/day with test age, up to 20%; it drives the "
        "confidence label and the P10/P90 value band. The 'now' point sits below the curve when the choke is "
        "holding the well back.",
    )

    st.subheader("Pipe friction learned from history")
    for pipe_id, (fit, (p_in, p_out, q)) in trace.pipe_fits.items():
        pipe = reg.network.pipe_by_id[pipe_id]
        prior = pipe.hydraulics
        with st.expander(
            f"{pipe_id}: K {prior.k_friction:.4f} → {fit.hydraulics.k_friction:.4f} ({fit.points} points, fit error {fit.rel_residual:.1%})"
        ):
            fig = go.Figure()
            fig.add_scatter(
                x=q,
                y=p_in**2 - prior.outlet_factor * p_out**2,
                mode="markers",
                name="historian samples",
                marker={"size": 4, "opacity": 0.5},
            )
            grid = np.linspace(max(q.min() * 0.8, 0), q.max() * 1.1, 100)
            fig.add_scatter(x=grid, y=[prior.dp2(x) for x in grid], name="registry prior", line={"dash": "dash"})
            fig.add_scatter(x=grid, y=[fit.hydraulics.dp2(x) for x in grid], name="fitted")
            fig.update_xaxes(title="flow (MMscfd)")
            fig.update_yaxes(title="P_in² − P_out² (bar²)")
            fig.update_layout(height=320)
            st.plotly_chart(fig, use_container_width=True)
    explain(
        "Why refit friction",
        "Lines foul, liquids accumulate and meters drift. The friction coefficient K is refitted by least squares "
        "on 14 days of Good pressure and flow samples, while the liquid-head term keeps its engineering prior. The "
        "fit is bounded to 0.5–2× the registry value and flagged if it hits the bound.",
    )
