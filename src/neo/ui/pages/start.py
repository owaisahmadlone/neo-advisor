"""Start here: the problem, how NeO answers it, and a guided tour of this UI."""

from __future__ import annotations

import streamlit as st

from neo.ui.common import DATA_DIR, RESULTS_DIR, registry
from neo.ui.netmap import network_figure

PIPELINE_DOT = """
digraph neo {
  rankdir=LR; node [shape=box, style="rounded,filled", fillcolor="#EEF3FB", fontname="Helvetica", fontsize=11];
  edge [fontname="Helvetica", fontsize=9, color="#5B6575"];
  src [label="Source systems\\nPI historian · well tests · lab\\nevents · nominations · contracts", fillcolor="#F3F4F6"];
  ingest [label="1 Ingest\\nschema checks, units"];
  gate [label="2 Data gate\\nbad / stale / stuck critical\\nsensor = no advisory", fillcolor="#FFF4E5"];
  cal [label="3 Calibrate\\nwell curves from tests\\npipe friction from history\\nrates reconciled to meters"];
  build [label="4 Build model\\nphysics + limits + contracts\\nas a linear program"];
  solve [label="5 Solve (3 priorities)\\n① hold step limits\\n② meet nominations\\n③ maximise value", fillcolor="#E8F5EC"];
  verify [label="6 Verify\\nre-check every limit with\\nexact physics", fillcolor="#FFF4E5"];
  explain [label="7 Explain\\nmerit order, binding limits,\\nreasons per well"];
  publish [label="8 Publish\\nadvisory · report ·\\nnomination draft · audit"];
  human [label="Planner reviews\\napprove / edit / reject", fillcolor="#F3F4F6"];
  src -> ingest -> gate -> cal -> build -> solve -> verify -> explain -> publish -> human;
  verify -> build [label="limit broken:\\ntighten and re-solve", style=dashed];
}
"""


def render() -> None:
    from neo.ui.nav import page

    st.title("NeO — which wells should flow tomorrow, and why")
    st.markdown(
        "The client runs an offshore **gas-condensate network**: wells on three platforms feed a shared "
        "compressor hub, two subsea trunk lines carry the gas to shore, a plant strips CO₂ to meet the sales "
        "specification, and the gas is sold to buyers under contracts while the liquids (condensate) go to a "
        "terminal. Every day before the nomination deadline, planners must decide **which wells flow, how hard, "
        "through which route, with how much compression, and which contract gets the gas**, without breaking any "
        "physical, safety or contractual limit. Today that takes days of spreadsheets; NeO does it in under a "
        "second and explains every choice."
    )
    st.plotly_chart(network_figure(registry(), height=430), use_container_width=True)
    st.caption("The synthetic reference network (hover over anything). Page 1 explains each element in detail.")

    st.subheader("How NeO turns data into advice")
    st.graphviz_chart(PIPELINE_DOT, use_container_width=True)
    c1, c2, c3 = st.columns(3)
    c1.markdown(
        "**Physics, not guesswork.** Each well, pipe and compressor follows textbook steady-state equations, "
        "tuned to the latest data. The model is linearised *conservatively*, so a plan that fits the model fits "
        "the real equations too."
    )
    c2.markdown(
        "**Priorities like a planner's.** First keep moves operable, then deliver what buyers nominated (highest "
        "priority buyer first), then make the most money: gas, condensate, minus partners' share and costs."
    )
    c3.markdown(
        "**Every answer explained.** The *merit order* says what one more unit from each well is worth to the "
        "whole network, and which limit (CO₂, compression, back-pressure…) holds it back."
    )

    st.subheader("Guided tour")
    st.markdown("Work through the pages in order; each one says what you're looking at and why it matters.")
    tour = [
        ("network", "1 · The network", "the assets, their limits and the physics behind them"),
        ("inputs", "2 · Inputs", "the raw files a real deployment would receive, warts and all"),
        ("gate", "3 · Data gate & calibration", "what NeO accepts, rejects and learns from those files"),
        (
            "advisory",
            "4 · Run an advisory",
            "change conditions (trip a compressor, cut CO₂ removal…) and watch the advice change",
        ),
        ("evaluate", "5 · Evaluate", "run NeO for 30 days on a simulated plant against incumbent practice"),
        ("dictionary", "Dictionary", "every field, code and term, searchable"),
    ]
    for path, title, why in tour:
        st.page_link(page(path), label=f"**{title}** — {why}")

    st.subheader("What is real and what is synthetic")
    st.markdown(
        "- **No client data is used anywhere.** The network, wells, contracts and all operating data are "
        "synthetic, drawn inside ranges from published sources (Malay Basin CO₂ levels, plant sizes, meter "
        "error bands). Values marked as assumptions in the work doc must be confirmed with the client.\n"
        "- The **truth plant** that generates the data deliberately differs from what NeO believes (well "
        "behaviour, pipe liquids, condensate yield), so NeO is never graded on its own model.\n"
        "- Results here prove the *method*; they are not a benefit claim for the client."
    )
    summary = next(
        (p for p in (DATA_DIR / "harness" / "summary.md", RESULTS_DIR / "harness-s7-30d" / "summary.md") if p.exists()),
        None,
    )
    if summary is not None:
        st.subheader("Latest 30-day evaluation")
        st.markdown(summary.read_text())
        st.caption(f"From `{summary.relative_to(summary.parents[2])}`. Page 5 shows every day and lets you re-run it.")
