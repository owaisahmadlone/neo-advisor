"""Plotly figures for the explainer UI."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from neo.domain.advisory import Advisory
from neo.domain.registry import Pipe, Registry, SalesSpec
from neo.explain.merit import MeritOrder
from neo.opt.pwl import Plane, chord_lines
from neo.physics.compressor import TrainMap
from neo.physics.pipes import PipeHydraulics
from neo.physics.wells import WellheadCurve
from neo.ui.common import COLORS
from neo.verify.forward import Check

LAYOUT = {"margin": {"l": 10, "r": 10, "t": 40, "b": 10}, "legend": {"orientation": "h", "y": -0.2}}


def _style(fig: go.Figure, title: str, height: int = 380) -> go.Figure:
    fig.update_layout(title=title, height=height, **LAYOUT)
    return fig


# --------------------------------------------------------------------- physics
def well_curves(
    curves: Mapping[str, Mapping[str, WellheadCurve]],
    *,
    points: Mapping[str, Sequence[tuple[float, float, str]]] | None = None,
    p_range: tuple[float, float] = (10.0, 150.0),
) -> go.Figure:
    """``curves``: well -> {label: curve}. ``points``: well -> [(pressure, rate, label)]."""
    fig = go.Figure()
    pressures = np.linspace(*p_range, 200)
    for well, by_label in curves.items():
        for label, curve in by_label.items():
            dash = "dash" if "registry" in label else "solid"
            fig.add_trace(
                go.Scatter(
                    x=pressures, y=[curve.rate(p) for p in pressures], name=f"{well} · {label}", line={"dash": dash}
                )
            )
    for well, pts in (points or {}).items():
        for p, q, label in pts:
            fig.add_trace(go.Scatter(x=[p], y=[q], mode="markers", marker={"size": 11}, name=f"{well} · {label}"))
    fig.update_xaxes(title="wellhead / manifold pressure (bara)")
    fig.update_yaxes(title="gas rate the well can deliver (MMscfd)")
    return _style(fig, "Well deliverability: less back-pressure, more gas", 420)


def pipe_drop(pipe: Pipe, hyd: PipeHydraulics, *, tol: float = 6.0, operating: float | None = None) -> go.Figure:
    q = np.linspace(0, pipe.capacity_mmscfd, 300)
    friction = [hyd.k_friction * x**2 for x in q]
    full = [hyd.dp2(x) for x in q]
    lines = chord_lines(hyd.dp2, 0.0, pipe.capacity_mmscfd, tol=tol)
    approx = [max(line(x) for line in lines) for x in q]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=q, y=friction, name="friction only (Weymouth)", line={"dash": "dot"}))
    fig.add_trace(go.Scatter(x=q, y=full, name="friction + liquid head (Flanigan)", line={"width": 3}))
    fig.add_trace(go.Scatter(x=q, y=approx, name="optimiser's chords (always ≥ physics)", line={"dash": "dash"}))
    if operating is not None:
        fig.add_vline(x=operating, line_dash="dot", annotation_text=f"plan {operating:.0f}")
    fig.update_xaxes(title="gas flow (MMscfd)")
    fig.update_yaxes(title="pressure-squared drop P_in² − P_out² (bar²)")
    return _style(fig, f"{pipe.id}: why low flow is not free (liquid head) and how the optimiser stays safe", 400)


def compressor_map(
    train: TrainMap,
    *,
    ambient_c: float,
    ps_range: tuple[float, float],
    pd_range: tuple[float, float],
    planes: Sequence[Plane] = (),
    point: tuple[float, float] | None = None,
) -> go.Figure:
    ps = np.linspace(*ps_range, 60)
    pd_ = np.linspace(*pd_range, 60)
    z = [[train.capacity_mmscfd(s, d, ambient_c) if s < d <= train.ratio_max * s else np.nan for s in ps] for d in pd_]
    fig = go.Figure(
        go.Heatmap(x=ps, y=pd_, z=z, colorscale="Viridis", colorbar={"title": "MMscfd per train"}, name="capacity")
    )
    fig.add_trace(
        go.Scatter(
            x=ps, y=train.ratio_max * ps, mode="lines", name=f"ratio limit {train.ratio_max:.1f}", line={"color": "red"}
        )
    )
    if planes:
        gap = [
            [
                train.capacity_mmscfd(s, d, ambient_c) - min(p(s**2, d**2) for p in planes)
                if s < d <= train.ratio_max * s
                else np.nan
                for s in ps
            ]
            for d in pd_
        ]
        fig.add_trace(
            go.Contour(x=ps, y=pd_, z=gap, showscale=False, contours_coloring="lines", name="safety gap", line_width=1)
        )
    if point is not None:
        fig.add_trace(
            go.Scatter(
                x=[point[0]],
                y=[point[1]],
                mode="markers",
                marker={"size": 14, "color": "white", "line": {"width": 2}},
                name="plan",
            )
        )
    fig.update_xaxes(title="suction pressure (bara)", range=list(ps_range))
    fig.update_yaxes(title="discharge pressure (bara)", range=list(pd_range))
    return _style(fig, "Compressor train capacity: higher suction or lower discharge = more gas", 440)


# --------------------------------------------------------------------- outputs
def merit_breakdown(merit: MeritOrder, *, top_reasons: int = 7) -> go.Figure:
    wells = [m for m in merit if m.status != "unavailable"]
    labels: dict[str, str] = {}
    totals: dict[str, float] = {}
    for m in wells:
        for r in m.reasons:
            labels[r.key] = r.label
            totals[r.key] = totals.get(r.key, 0.0) + abs(r.usd_per_mmscfd)
    keep = sorted(totals, key=totals.get, reverse=True)[:top_reasons]
    order = [m.well_id for m in wells]
    fig = go.Figure()
    for key in keep:
        values = [next((r.usd_per_mmscfd for r in m.reasons if r.key == key), 0.0) for m in wells]
        fig.add_trace(go.Bar(y=order, x=values, name=labels[key], orientation="h"))
    other = [sum(r.usd_per_mmscfd for r in m.reasons if r.key not in keep) for m in wells]
    if any(abs(v) > 1 for v in other):
        fig.add_trace(go.Bar(y=order, x=other, name="other limits", orientation="h", marker_color="#BBB"))
    fig.add_trace(
        go.Scatter(
            y=order,
            x=[m.unit_value for m in wells],
            mode="markers",
            marker={"symbol": "diamond", "size": 12, "color": "black"},
            name="net unit value",
        )
    )
    fig.update_layout(barmode="relative")
    fig.update_yaxes(autorange="reversed", title="rank (1 = most valuable)")
    fig.update_xaxes(title="USD per day per extra MMscfd from this well")
    return _style(fig, "Why each well ranks where it does", max(420, 28 * len(wells)))


def value_waterfall(advisory: Advisory) -> go.Figure:
    lines = advisory.value_waterfall
    base = sum(v.baseline_usd_d for v in lines)
    plan = sum(v.plan_usd_d for v in lines)
    names = ["hold current settings", *(v.component.replace("_", " ") for v in lines), "NeO plan"]
    values = [base, *(v.delta_usd_d for v in lines), plan]
    measure = ["absolute", *(["relative"] * len(lines)), "total"]
    fig = go.Figure(go.Waterfall(x=names, y=values, measure=measure, text=[f"{v:,.0f}" for v in values]))
    fig.update_yaxes(title="USD per day")
    return _style(fig, "Where the plan's value comes from, against holding today's settings")


def check_margins(checks: Iterable[Check]) -> go.Figure:
    rows = [
        {
            "limit": c.limit,
            "subject": c.subject,
            "used": min(c.utilisation, 1.5) * 100,
            "status": "breached" if not c.ok else ("warning" if c.severity == "warning" else "ok"),
            "value": c.value,
            "bound": c.bound,
        }
        for c in checks
        if np.isfinite(c.utilisation)
    ]
    df = pd.DataFrame(rows)
    if df.empty:
        return go.Figure()
    df["item"] = df["limit"] + " · " + df["subject"].astype(str)
    df = df.sort_values("used", ascending=False)
    fig = px.bar(
        df,
        x="used",
        y="item",
        color="status",
        orientation="h",
        hover_data=["value", "bound"],
        color_discrete_map={"ok": COLORS["ok"], "warning": COLORS["warn"], "breached": COLORS["bad"]},
    )
    fig.add_vline(x=100, line_dash="dash", line_color="red", annotation_text="limit")
    fig.update_xaxes(title="% of limit used (lower limits: % of floor needed)")
    fig.update_yaxes(title="", autorange="reversed")
    return _style(fig, "Every hard limit, re-checked with the exact physics", max(420, 18 * len(df)))


def contracts(advisory: Advisory) -> go.Figure:
    fig = go.Figure()
    names = [a.contract_id for a in advisory.allocations]
    fig.add_trace(go.Bar(x=names, y=[a.nominated_mmbtu_d for a in advisory.allocations], name="nominated"))
    fig.add_trace(go.Bar(x=names, y=[a.planned_mmbtu_d for a in advisory.allocations], name="planned delivery"))
    fig.add_trace(
        go.Bar(
            x=[e.outlet_id for e in advisory.excess],
            y=[e.planned_mmbtu_d for e in advisory.excess],
            name="excess sold (POS)",
        )
    )
    fig.update_yaxes(title="MMBtu/d")
    return _style(fig, "Contracts: what buyers asked for vs what the plan delivers", 340)


def sales_quality(advisory: Advisory, spec: SalesSpec) -> go.Figure:
    s = advisory.sales_gas
    fig = go.Figure()
    fig.add_trace(
        go.Indicator(
            mode="gauge+number",
            value=s.co2_mol_pct,
            title={"text": "sales CO2 (mol%)"},
            gauge={
                "axis": {"range": [0, spec.co2_max * 150]},
                "threshold": {"value": spec.co2_max * 100, "line": {"color": "red"}},
            },
            domain={"x": [0, 0.45], "y": [0, 1]},
        )
    )
    fig.add_trace(
        go.Indicator(
            mode="gauge+number",
            value=s.ghv_btu_scf,
            title={"text": "sales heating value (Btu/scf)"},
            gauge={
                "axis": {"range": [spec.ghv_min_btu_scf - 100, spec.ghv_max_btu_scf + 100]},
                "steps": [{"range": [spec.ghv_min_btu_scf, spec.ghv_max_btu_scf], "color": "#D8EFD9"}],
            },
            domain={"x": [0.55, 1], "y": [0, 1]},
        )
    )
    return _style(fig, "Sales gas against its specification (green band / red line = limit)", 280)


# --------------------------------------------------------------------- harness
def harness_values(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for policy in ("advisor", "hold", "greedy"):
        fig.add_trace(
            go.Scatter(x=df["gas_day"], y=df[f"{policy}_value_usd"], name=policy, line={"color": COLORS[policy]})
        )
    for day in df.loc[df["advisory_status"] == "blocked", "gas_day"]:
        fig.add_vrect(x0=day, x1=day, line_width=8, line_color=COLORS["bad"], opacity=0.25, annotation_text="blocked")
    fig.update_yaxes(title="value earned on the true plant (USD/d)")
    return _style(fig, "Daily value: NeO vs the two incumbent practices")


def harness_uplift(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for base in ("hold", "greedy"):
        delta = df["advisor_value_usd"] - df[f"{base}_value_usd"]
        fig.add_trace(go.Bar(x=df["gas_day"], y=delta, name=f"NeO − {base}", marker_color=COLORS[base]))
    fig.add_hline(y=0, line_color="black")
    fig.update_layout(barmode="group")
    fig.update_yaxes(title="USD/d (positive = NeO better)")
    return _style(fig, "Daily uplift, same day, same plant (paired comparison)")


def harness_shortfall(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for policy in ("advisor", "hold", "greedy"):
        fig.add_trace(
            go.Scatter(x=df["gas_day"], y=df[f"{policy}_shortfall_mmbtu"], name=policy, line={"color": COLORS[policy]})
        )
    fig.update_yaxes(title="gas below nominations (MMBtu/d)")
    return _style(fig, "Shortfall against buyers' nominations (lower is better)")


def tag_series(samples: pd.DataFrame, tag: str, cutoff: pd.Timestamp | None = None) -> go.Figure:
    rows = samples.loc[samples["tag"] == tag]
    fig = px.scatter(
        rows,
        x="ts_utc",
        y="value",
        color="quality",
        color_discrete_map={"Good": COLORS["gas"], "Questionable": COLORS["warn"], "Bad": COLORS["bad"]},
        hover_data=["substituted", "digital_state"],
    )
    fig.update_traces(marker={"size": 4})
    if cutoff is not None:
        fig.add_vline(x=cutoff, line_dash="dash", annotation_text="cut-off")
    unit = rows["uom"].iloc[0] if not rows.empty else ""
    fig.update_yaxes(title=f"{tag} ({unit})")
    return _style(fig, f"{tag}: every historian sample, coloured by quality", 340)


def reg_summary(reg: Registry) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"element": "wells", "count": len(reg.network.wells)},
            {"element": "platforms", "count": len(reg.network.platforms)},
            {"element": "pipes", "count": len(reg.network.pipes)},
            {"element": "compressor trains", "count": reg.network.compressor.trains},
            {"element": "contracts", "count": len(reg.commercial.contracts)},
            {"element": "PSC blocks", "count": len(reg.psc.blocks)},
        ]
    )
