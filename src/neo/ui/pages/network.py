"""Page 1: the network as the advisor knows it (the engineering and commercial registers)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from neo.domain.fixtures import DESIGN_MANIFOLD_BARA
from neo.opt.pwl import conservative_planes
from neo.ui import charts
from neo.ui.common import COLORS, column_help, explain, page_header, registry
from neo.ui.netmap import network_figure


def _registry_frame(models, group: str) -> tuple[pd.DataFrame, dict]:
    df = pd.DataFrame([m.model_dump() for m in models])
    return df, column_help("REGISTRY", group, df.columns)


def render() -> None:
    reg = registry()
    net = reg.network
    page_header(
        "1 · The network",
        "The *registers*: the engineering description of every asset and limit, plus the commercial terms. "
        "In a deployment these come from PI Asset Framework, engineering files, the GSA register and the "
        f"approved-limits register. Here: `configs/reference/` (version {net.version}, synthetic).",
        "Everything NeO decides is bounded by these numbers. If a limit here is wrong, the advice is wrong, so "
        "each one needs a named owner at the client (open question in the work doc).",
    )
    st.plotly_chart(network_figure(reg), use_container_width=True)
    explain(
        "How to read the map",
        "- **Coloured dots on the left** are wells, grouped by platform (P1 red: lean, high-CO₂; P2 green: "
        "condensate-rich, low-CO₂; P3 orange: high-pressure).\n"
        "- **Platform manifolds** collect the wells; flowlines take the gas to the **hub suction**.\n"
        "- The **compressor station** (purple) boosts pressure so the gas can travel ~150 km through the two "
        "**trunk lines** to the **slug catcher** onshore. Third-party gas (PAC-1) shares the compressor.\n"
        "- P3 is high-pressure: it can route **into the hub suction** (uses compression) or **bypass into the "
        "discharge** (saves compression, but the wells then push against a higher pressure). That choice is one "
        "of NeO's decisions.\n"
        "- The **gas plant** removes CO₂ to meet the sales spec and sends gas to the buyers on the right; "
        "condensate goes to the terminal.",
        expanded=True,
    )

    tabs = st.tabs(
        [
            "Wells",
            "Platforms & pipelines",
            "Compressor",
            "Gas plant & quality",
            "Liquids & hydrates",
            "Contracts & PSC",
            "Gas day",
        ]
    )

    with tabs[0]:
        df, cfg = _registry_frame(net.wells, "Well")
        df["raw GHV (Btu/scf)"] = [w.composition.ghv_btu_scf for w in net.wells]
        df["design rate (MMscfd)"] = [w.curve.rate(DESIGN_MANIFOLD_BARA[w.platform]) for w in net.wells]
        st.dataframe(df, column_config=cfg, use_container_width=True, hide_index=True)
        st.caption("Hover over a column header for what it means, its unit, where it comes from and how NeO uses it.")
        c1, c2 = st.columns(2)
        with c1:
            fig = px.scatter(
                df,
                x="co2",
                y="cgr_bbl_mmscf",
                size="design rate (MMscfd)",
                color="platform",
                hover_name="id",
                color_discrete_map={k: COLORS[k] for k in ("P1", "P2", "P3")},
            )
            fig.update_xaxes(title="CO₂ in the gas (mole fraction)", tickformat=".0%")
            fig.update_yaxes(title="condensate per MMscf of gas (bbl)")
            fig.update_layout(title="The trade-off: CO₂ costs plant capacity, condensate earns money", height=380)
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            chosen = st.multiselect("Wells to plot", [w.id for w in net.wells], default=["P1-W03", "P2-W01", "P3-W01"])
            curves = {w: {"registry curve": net.well_by_id[w].curve} for w in chosen}
            st.plotly_chart(charts.well_curves(curves), use_container_width=True)
        explain(
            "The wellhead deliverability curve",
            "Each well is described by **q = C·(p_ws² − p_wh²)ⁿ**: the rate it gives when its wellhead pressure "
            "is p_wh. p_ws is the pressure with the well shut in. The lower the pressure downstream "
            "(back-pressure), the more gas flows, which is why one well's flow affects every other well on the "
            "same line. The choke (an adjustable valve) can only *reduce* flow below this curve. "
            "`min_rate_mmscfd` is the Turner critical rate: below it the well cannot lift its liquids and dies. "
            "`rate_cap_mmscfd` is the approved maximum (sand/drawdown limit).",
        )

    with tabs[1]:
        df, cfg = _registry_frame(net.platforms, "Platform")
        df["export_options"] = df["export_options"].map(lambda v: " or ".join(v))
        df["hydrate"] = df["hydrate"].map(lambda h: f"T_h = {h['a']} + {h['b']}·ln P")
        st.dataframe(df, column_config=cfg, use_container_width=True, hide_index=True)
        dfp, cfgp = _registry_frame(net.pipes, "Pipe")
        dfp["K (bar²/MMscfd²)"] = [p.hydraulics.k_friction for p in net.pipes]
        st.dataframe(dfp, column_config=cfgp, use_container_width=True, hide_index=True)
        node_df, node_cfg = _registry_frame(net.nodes, "Node")
        st.dataframe(node_df, column_config=node_cfg, use_container_width=True, hide_index=True)
        pipe_id = st.selectbox("Pipe to inspect", [p.id for p in net.pipes], index=4)
        pipe = net.pipe_by_id[pipe_id]
        st.plotly_chart(charts.pipe_drop(pipe, pipe.hydraulics), use_container_width=True)
        explain(
            "Why pressure-squared, and what the dashed line is",
            "Gas pipeline physics is naturally written in **P²**: the drop P_in² − P_out² grows with the square of "
            "flow (friction, Weymouth equation). These wet-gas lines also carry liquid; at low flow the liquid "
            "pools and adds a hydrostatic head (Flanigan correlation), so the drop does **not** fall to zero. "
            "That is the U-shape.\n\n"
            "The optimiser needs straight lines. It replaces the curve with **chords** that always sit *above* the "
            "true drop (dashed), so the optimiser can only over-estimate pressure loss. A plan that is feasible "
            "for the optimiser is therefore feasible in the real physics. The verifier (page 4) re-checks this.\n\n"
            "**Pressure roles.** `setpoint` nodes (hub suction, slug-catcher arrival) are held by control "
            "systems; `free` nodes float with the flow. Lower pressure limits are only enforced at set-point "
            "nodes, which is what makes the chord approximation safe.",
        )

    with tabs[2]:
        comp = net.compressor
        st.dataframe(
            pd.DataFrame([comp.train.model_dump()]),
            column_config=column_help("REGISTRY", "TrainSpec", comp.train.model_dump()),
            use_container_width=True,
            hide_index=True,
        )
        st.dataframe(
            pd.DataFrame([comp.fuel.model_dump()]),
            column_config=column_help("REGISTRY", "FuelSpec", comp.fuel.model_dump()),
            use_container_width=True,
            hide_index=True,
        )
        station = comp.model_dump(exclude={"train", "fuel"})
        st.dataframe(
            pd.DataFrame([station]),
            column_config=column_help("REGISTRY", "CompressorStation", station),
            use_container_width=True,
            hide_index=True,
        )
        ambient = st.slider("Ambient temperature (°C) — hot days derate the gas turbines", 24.0, 38.0, 31.0, 0.5)
        hub = net.node_by_id[comp.suction_node]
        train = comp.train.map
        planes = conservative_planes(
            lambda ps, pd_: train.capacity_mmscfd(ps, pd_, ambient),
            ps_range=(hub.p_min_bara, hub.p_max_bara),
            pd_range=(net.arrival_node.setpoint_bara, comp.pd_max_bara),
            ratio_max=train.ratio_max,
        )
        st.plotly_chart(
            charts.compressor_map(
                train,
                ambient_c=ambient,
                ps_range=(hub.p_min_bara, hub.p_max_bara),
                pd_range=(net.arrival_node.setpoint_bara, comp.pd_max_bara),
                planes=planes,
            ),
            use_container_width=True,
        )
        explain(
            "Reading the compressor map",
            "Colour = how much gas **one train** can move (MMscfd) at a given suction and discharge pressure. "
            "Capacity is limited by the gas turbine's power (falls as the pressure ratio rises and on hot days) "
            "and by the machine's maximum volume (stonewall, which rises with suction pressure because gas is "
            "denser). Above the red line the ratio is too high to run.\n\n"
            "This is why, when a train trips, NeO often **raises** the suction set-point: each remaining train "
            "then moves more gas, at the cost of more back-pressure on the wells. The contour lines show the "
            "safety gap between the true map and the flat planes the optimiser uses (planes always sit *below* "
            "the map).",
        )

    with tabs[3]:
        plant = net.plant
        st.dataframe(
            pd.DataFrame([plant.model_dump(exclude={"spec"})]),
            column_config=column_help("REGISTRY", "Plant", plant.model_dump(exclude={"spec"})),
            use_container_width=True,
            hide_index=True,
        )
        st.dataframe(
            pd.DataFrame([plant.spec.model_dump()]),
            column_config=column_help("REGISTRY", "SalesSpec", plant.spec.model_dump()),
            use_container_width=True,
            hide_index=True,
        )
        third = pd.DataFrame([s.model_dump() for s in net.third_party])
        st.dataframe(
            third,
            column_config=column_help("REGISTRY", "ThirdPartyStream", third.columns),
            use_container_width=True,
            hide_index=True,
        )
        explain(
            "How CO₂ and heating value constrain production",
            "All gas (including third-party gas) is blended at one plant, so the inlet composition is simply the "
            "flow-weighted average of the wells (molar blending, ISO 6976). The plant can remove at most "
            f"{plant.co2_removal_cap_mmscfd:.0f} MMscfd of CO₂; the sales gas must be ≤ {plant.spec.co2_max:.0%} CO₂ "
            f"and between {plant.spec.ghv_min_btu_scf:.0f} and {plant.spec.ghv_max_btu_scf:.0f} Btu/scf. High-CO₂ "
            "P1 wells eat removal capacity, so when it is full the cheapest way to deliver more energy is to choke "
            "P1 and open low-CO₂ wells. That is the most common binding limit in the results. Off-spec gas is "
            "treated as a hard limit because it trips downstream plants.",
        )

    with tabs[4]:
        sc, meg = net.slug_catcher, net.meg
        st.dataframe(
            pd.DataFrame([sc.model_dump()]),
            column_config=column_help("REGISTRY", "SlugCatcher", sc.model_dump()),
            use_container_width=True,
            hide_index=True,
        )
        st.dataframe(
            pd.DataFrame([meg.model_dump()]),
            column_config=column_help("REGISTRY", "MegSystem", meg.model_dump()),
            use_container_width=True,
            hide_index=True,
        )
        pressures = np.linspace(20, 100, 100)
        fig = go.Figure()
        for platform in net.platforms:
            curve = platform.hydrate.curve
            fig.add_trace(
                go.Scatter(
                    x=pressures, y=[curve.temperature_c(p) for p in pressures], name=f"{platform.id} hydrate line"
                )
            )
        fig.add_hline(y=meg.seabed_temp_c, line_dash="dash", annotation_text="seabed temperature")
        fig.update_xaxes(title="pressure (bara)")
        fig.update_yaxes(title="hydrate formation temperature (°C)")
        fig.update_layout(
            title="Hydrates form below the line; warm Malay Basin seabed keeps lines mostly safe", height=360
        )
        st.plotly_chart(fig, use_container_width=True)
        explain(
            "Liquids, slug catcher and hydrates",
            "Every MMscf of gas brings condensate (CGR) and water (WGR). The slug catcher onshore can handle "
            f"{sc.liquid_cap_bbld:,.0f} bbl/d. When rates rise, the faster gas sweeps liquid that was lying in the "
            f"trunk lines into the catcher; that surge must fit in its free volume ({sc.free_volume_bbl:,.0f} bbl × "
            f"{sc.sweep_fraction:.0%}). Hydrates (ice-like solids) form if the gas is too cold at its pressure; MEG "
            "is injected to prevent them, in proportion to the water produced. Big pressure drops across chokes "
            "also cool the gas (Joule-Thomson), which the verifier flags per well.",
        )

    with tabs[5]:
        contracts = pd.DataFrame([c.model_dump() for c in reg.commercial.contracts])
        st.dataframe(
            contracts,
            column_config=column_help("REGISTRY", "Contract", contracts.columns),
            use_container_width=True,
            hide_index=True,
        )
        psc = pd.DataFrame([b.model_dump() for b in reg.psc.blocks])
        st.dataframe(
            psc,
            column_config=column_help("REGISTRY", "PscBlock", psc.columns),
            use_container_width=True,
            hide_index=True,
        )
        st.metric("Condensate price assumption", f"${reg.commercial.condensate_usd_bbl:.0f}/bbl")
        explain(
            "How contracts and PSC terms shape the plan",
            "**GSA** contracts (Gas Sales Agreements) set a daily contract quantity (DCQ) and a price; each day the "
            "buyer *nominates* what it wants. NeO first minimises shortfall against nominations, with the "
            "priority-1 buyer weighted ten times the priority-2 buyer, and only then maximises value. That is "
            "how the Malaysian precedents (Sarawak RTO, Selot et al. 2008) order it. Gas beyond nominations can "
            "go to the **priority-of-supply** LNG outlet up to its cap.\n\n"
            "**PSC netback**: each block's wells earn the enterprise only its share of revenue (after cost "
            "recovery, profit split and tax). An extra MMscf is worth different amounts in different blocks. "
            "All values here are synthetic; the real terms are an open question for the client.",
        )

    with tabs[6]:
        cal = net.calendar
        st.json(net.gas_day.model_dump())
        st.markdown(
            f"A **gas day** runs from {cal.start_hour:02d}:00 to {cal.start_hour:02d}:00 local time "
            f"({cal.timezone}). The advice for gas day D is produced from data up to the **cut-off**, "
            f"{cal.nomination_cutoff_hour:02d}:00 local on D−1, before buyers' nominations are due. Historians record "
            "UTC; NeO converts. Both hours are assumptions to confirm with the client."
        )
