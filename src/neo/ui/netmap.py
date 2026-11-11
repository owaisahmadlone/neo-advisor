"""Schematic network map: wells -> platforms -> hub compressor -> trunks -> slug catcher -> plant -> buyers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import plotly.graph_objects as go

from neo.domain.registry import Registry
from neo.ui.common import COLORS

PLATFORM_Y = (7.0, 4.2, 1.4)
WELL_X = 0.6
MANIFOLD_X = 3.0
HUB_SUCTION = (6.0, 5.4)
COMPRESSOR = (7.2, 5.4)
HUB_DISCHARGE = (8.4, 5.4)
THIRD_PARTY = (6.0, 8.0)
ARRIVAL = (12.4, 5.4)
PLANT = (13.9, 5.4)
TERMINAL = (13.9, 2.6)
BUYER_X = 16.0


@dataclass(slots=True)
class MapData:
    """Optional overlays; leave empty to draw the registry only."""

    well_rates: Mapping[str, float] = field(default_factory=dict)
    arc_flow: Mapping[str, float] = field(default_factory=dict)
    pressures: Mapping[str, float] = field(default_factory=dict)
    routes: Mapping[str, str] = field(default_factory=dict)
    hub_inflow: float | None = None
    plant_inlet: float | None = None
    third_party: Mapping[str, float] = field(default_factory=dict)
    deliveries: Mapping[str, float] = field(default_factory=dict)  # contract id -> MMBtu/d
    condensate_bbld: float | None = None
    trains: int | None = None
    highlight: set[str] = field(default_factory=set)


def _positions(reg: Registry) -> dict[str, tuple[float, float]]:
    net = reg.network
    pos: dict[str, tuple[float, float]] = {}
    for platform, y in zip(net.platforms, PLATFORM_Y, strict=False):
        pos[platform.manifold] = (MANIFOLD_X, y)
        wells = net.wells_on(platform.id)
        span = 1.1 if len(wells) > 1 else 0.0
        for i, well in enumerate(wells):
            offset = -span + 2 * span * i / max(len(wells) - 1, 1)
            pos[well.id] = (WELL_X, y + offset)
    comp = net.compressor
    pos[comp.suction_node] = HUB_SUCTION
    pos[comp.id] = COMPRESSOR
    pos[comp.discharge_node] = HUB_DISCHARGE
    for i, stream in enumerate(net.third_party):
        pos[stream.id] = (THIRD_PARTY[0] - 0.8 * i, THIRD_PARTY[1])
    pos[net.arrival_node.id] = ARRIVAL
    pos[net.plant.id] = PLANT
    pos["terminal"] = TERMINAL
    contracts = reg.commercial.contracts
    for i, contract in enumerate(contracts):
        pos[contract.id] = (BUYER_X, 7.0 - 1.6 * i)
    return pos


def _width(value: float | None, scale: float) -> float:
    if value is None:
        return 2.0
    return 1.0 + 11.0 * max(value, 0.0) / scale if scale > 0 else 2.0


def _line(
    fig: go.Figure,
    a: tuple[float, float],
    b: tuple[float, float],
    *,
    width: float,
    color: str,
    hover: str,
    label: str = "",
    bend: float = 0.0,
    dash: str = "solid",
) -> None:
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2 + bend)
    xs, ys = [a[0], mid[0], b[0]], [a[1], mid[1], b[1]]
    fig.add_trace(
        go.Scatter(
            x=xs,
            y=ys,
            mode="lines",
            line={"width": width, "color": color, "dash": dash, "shape": "spline"},
            hoverinfo="text",
            text=[hover] * 3,
            showlegend=False,
        )
    )
    if label:
        fig.add_annotation(x=mid[0], y=mid[1], text=label, showarrow=False, font={"size": 10}, yshift=10)


def network_figure(reg: Registry, data: MapData | None = None, *, height: int = 560) -> go.Figure:
    data = data or MapData()
    net = reg.network
    pos = _positions(reg)
    fig = go.Figure()
    flows = [*data.well_rates.values(), *data.arc_flow.values(), data.hub_inflow or 0.0]
    scale = max(flows) if flows and max(flows) > 0 else 1.0
    pressure = data.pressures

    def p_text(node: str) -> str:
        return f"{pressure[node]:.1f} bara" if node in pressure else "pressure: n/a"

    # wells -> manifold
    for well in net.wells:
        platform = net.platform_by_id[well.platform]
        q = data.well_rates.get(well.id)
        color = COLORS.get(well.platform, COLORS["neutral"])
        dash = "dot" if q is not None and q <= 0 else "solid"
        hover = f"{well.id}: {q:.1f} MMscfd" if q is not None else f"{well.id}"
        _line(
            fig, pos[well.id], pos[platform.manifold], width=_width(q, scale) * 0.6, color=color, hover=hover, dash=dash
        )

    # export lines (platform routes, trunks)
    chosen = dict(data.routes)
    for pipe in net.pipes:
        a, b = pos[pipe.from_node], pos[pipe.to_node]
        q = data.arc_flow.get(pipe.id)
        platform = next((p.id for p in net.platforms if pipe.id in p.export_options), None)
        unused = platform is not None and chosen and chosen.get(platform) != pipe.id
        bend = 0.0
        if pipe.parallel_group:
            siblings = [p.id for p in net.pipes if p.parallel_group == pipe.parallel_group]
            bend = 0.7 if siblings.index(pipe.id) == 0 else -0.7
        hover = (
            f"{pipe.id}: {pipe.length_km:.0f} km × {pipe.diameter_in:.0f} in, capacity {pipe.capacity_mmscfd:.0f} MMscfd"
            + (f"<br>flow {q:.1f} MMscfd" if q is not None else "")
        )
        label = f"{pipe.id}" + (f" · {q:.0f}" if q is not None and not unused else "")
        _line(
            fig,
            a,
            b,
            width=_width(None if unused else q, scale),
            color="#C4CAD3" if unused else "#3B5B92",
            hover=hover + ("<br>(route not used)" if unused else ""),
            label=label,
            bend=bend,
            dash="dash" if unused else "solid",
        )

    comp = net.compressor
    hub = data.hub_inflow
    trains = f"{data.trains} trains" if data.trains is not None else f"{comp.trains} trains"
    _line(
        fig,
        pos[comp.suction_node],
        pos[comp.discharge_node],
        width=_width(hub, scale),
        color="#6B4FBB",
        hover=f"{comp.id}: {trains}" + (f", inflow {hub:.0f} MMscfd" if hub is not None else ""),
    )
    for stream in net.third_party:
        rate = data.third_party.get(stream.id)
        _line(
            fig,
            pos[stream.id],
            pos[stream.node],
            width=_width(rate, scale),
            color="#A0A7B4",
            hover=f"{stream.id} (third-party gas): {rate:.0f} MMscfd" if rate is not None else stream.id,
        )
    plant_q = data.plant_inlet
    _line(
        fig,
        pos[net.arrival_node.id],
        pos[net.plant.id],
        width=_width(plant_q, scale),
        color="#3B5B92",
        hover=f"plant inlet {plant_q:.0f} MMscfd" if plant_q is not None else "slug catcher → gas plant",
    )
    max_energy = max(data.deliveries.values(), default=0.0) or 1.0
    for contract in reg.commercial.contracts:
        energy = data.deliveries.get(contract.id)
        width = 1.0 + 8.0 * energy / max_energy if energy is not None else 2.0
        _line(
            fig,
            pos[net.plant.id],
            pos[contract.id],
            width=width,
            color=COLORS["gas"],
            hover=f"{contract.id}: {energy:,.0f} MMBtu/d" if energy is not None else contract.id,
            label=f"{energy / 1000:,.0f} BBtu/d" if energy else "",
        )
    cond = data.condensate_bbld
    _line(
        fig,
        pos[net.plant.id],
        pos["terminal"],
        width=3.0,
        color=COLORS["condensate"],
        hover=f"condensate {cond:,.0f} bbl/d" if cond is not None else "condensate to terminal",
        label=f"{cond:,.0f} bbl/d" if cond else "",
    )

    # nodes
    nodes = []
    for well in net.wells:
        nodes.append(
            (
                well.id,
                "well",
                COLORS.get(well.platform, COLORS["neutral"]),
                9,
                f"{well.id}<br>CO2 {well.co2:.0%}, CGR {well.cgr_bbl_mmscf:.0f}",
            )
        )
    for platform in net.platforms:
        node = platform.manifold
        nodes.append(
            (
                node,
                "manifold",
                COLORS.get(platform.id, COLORS["neutral"]),
                18,
                f"{platform.id} manifold {node}<br>{p_text(node)}",
            )
        )
    nodes += [
        (comp.suction_node, "hub", "#6B4FBB", 16, f"hub suction<br>{p_text(comp.suction_node)}"),
        (comp.id, "compressor", "#6B4FBB", 24, f"compressor station {comp.id}<br>{trains}"),
        (comp.discharge_node, "hub", "#6B4FBB", 16, f"hub discharge<br>{p_text(comp.discharge_node)}"),
        (
            net.arrival_node.id,
            "arrival",
            "#3B5B92",
            20,
            f"slug catcher (arrival) {net.arrival_node.id}<br>{p_text(net.arrival_node.id)}",
        ),
        (net.plant.id, "plant", "#3B5B92", 24, f"gas plant {net.plant.id}<br>CO2 removal, sales spec"),
        ("terminal", "terminal", COLORS["condensate"], 16, "condensate terminal"),
    ]
    nodes += [(s.id, "third_party", "#A0A7B4", 14, f"{s.id}: third-party gas") for s in net.third_party]
    nodes += [(c.id, "buyer", COLORS["gas"], 16, f"{c.id}: {c.buyer}") for c in reg.commercial.contracts]
    fig.add_trace(
        go.Scatter(
            x=[pos[n][0] for n, *_ in nodes],
            y=[pos[n][1] for n, *_ in nodes],
            mode="markers+text",
            marker={
                "size": [s for *_, s, _ in nodes],
                "color": [c for _, _, c, _, _ in nodes],
                "line": {"width": [3 if n in data.highlight else 1 for n, *_ in nodes], "color": "#111"},
            },
            text=[n if kind != "well" else n.split("-")[1] for n, kind, *_ in nodes],
            textposition=["middle left" if kind == "well" else "bottom center" for _, kind, *_ in nodes],
            hovertext=[h for *_, h in nodes],
            hoverinfo="text",
            showlegend=False,
        )
    )
    fig.update_layout(
        height=height,
        margin={"l": 10, "r": 10, "t": 30, "b": 10},
        xaxis={"visible": False, "range": [-0.6, 17.2]},
        yaxis={"visible": False, "range": [-0.2, 8.8]},
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig
