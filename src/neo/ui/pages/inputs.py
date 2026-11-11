"""Page 2: the landing zone, file by file, exactly as a deployment would receive it."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from neo.ingest import schema
from neo.ingest.landing import LandingZone
from neo.ui import charts
from neo.ui.common import (
    REGISTRY_DIR,
    column_help,
    doc,
    explain,
    extract_doc,
    fresh_ui_dir,
    page_header,
    zone_selector,
)


def _generate_box() -> None:
    from neo.synth import SCENARIOS, generate

    with st.expander("➕ Generate a new landing zone (synthetic truth plant)"):
        st.markdown(
            "Runs the simulated plant on fixed settings and writes every extract. Scenarios add events: "
            "`trip` (a compressor train trips on day 3), `co2cut` (CO₂ removal derated on day 3), `badtag` "
            "(a critical pressure sensor goes Bad before day 4's cut-off)."
        )
        c1, c2, c3, c4 = st.columns(4)
        scenario = c1.selectbox("Scenario", sorted(SCENARIOS))
        days = c2.number_input("Days", 3, 60, 10)
        seed = c3.number_input("Seed", 0, 10_000, 7)
        start = c4.date_input("Start", date(2026, 11, 1))
        if st.button("Generate", type="primary"):
            target = fresh_ui_dir(f"{scenario}-s{seed}-{days}d")
            with st.spinner("Simulating the plant and writing extracts…"):
                generate(
                    REGISTRY_DIR, target / "landing", start=start, days=int(days), scenario=scenario, seed=int(seed)
                )
            st.session_state["zone_name"] = str((target / "landing").relative_to(target.parents[1]))
            st.success(f"Written to {target / 'landing'}")
            st.rerun()


def _extract_header(key: str) -> None:
    extract = extract_doc(key)
    if extract is None:
        return
    st.markdown(
        f"**{extract.title}** — stands in for *{extract.mimics}*. Grain: {extract.grain}.  \n"
        f"**Why NeO needs it:** {extract.why}"
    )


def _show(df: pd.DataFrame, key: str) -> None:
    st.dataframe(df, column_config=column_help("EXTRACTS", key, df.columns), use_container_width=True, hide_index=True)


def render() -> None:
    page_header(
        "2 · Inputs: the landing zone",
        "The raw files NeO reads, in the layout real source systems would deliver: PI historian samples with "
        "quality codes, well tests, lab samples, events, nominations, prices, third-party schedules, terminal "
        "plans and lagged allocations. They are written by the synthetic truth plant, noise included.",
        "Real data is messy: sensors drift, freeze or go Bad, tests are monthly and noisy, third parties don't "
        "always share schedules. A deployable advisor must cope, and this is what it copes with.",
    )
    _generate_box()
    zone_dir = zone_selector()
    if zone_dir is None:
        return
    zone = LandingZone(zone_dir)
    files = sorted(p for p in zone_dir.rglob("*") if p.is_file())
    st.caption(f"{len(files)} files in `{zone_dir}`")

    tabs = st.tabs(
        [
            "Historian",
            "Tag map",
            "Well tests",
            "Lab samples",
            "Events",
            "Nominations",
            "Prices",
            "Third-party",
            "Terminal",
            "Allocation (DOR)",
            "Files",
        ]
    )
    with tabs[0]:
        _extract_header("historian")
        hist_files = sorted((zone_dir / schema.HISTORIAN_DIR).glob("*.parquet"))
        if not hist_files:
            st.warning("No historian files.")
        else:
            first, last = pd.Timestamp(hist_files[0].stem, tz="UTC"), pd.Timestamp(hist_files[-1].stem, tz="UTC")
            samples = zone.historian(first.to_pydatetime(), (last + pd.Timedelta(days=1)).to_pydatetime())
            tags = sorted(samples["tag"].unique())
            network_tags = [t for t in tags if not t.startswith(("P1-", "P2-", "P3-"))]
            c1, c2 = st.columns([1, 3])
            group = c1.radio("Tag group", ["network", "wells"])
            tag = c1.selectbox(
                "Tag", network_tags if group == "network" else [t for t in tags if t not in network_tags]
            )
            pattern = _tag_pattern(tag)
            c1.markdown(f"**{pattern}** — {_tag_meaning(pattern)}")
            critical = tag in zone.critical_tags
            c1.markdown(
                "🔴 **critical**: a Bad/stale/stuck reading blocks the advisory."
                if critical
                else "⚪ non-critical: gaps are substituted and flagged."
            )
            rows = samples.loc[samples["tag"] == tag]
            quality = rows["quality"].value_counts(normalize=True).mul(100).round(1)
            c1.dataframe(quality.rename("% of samples"))
            c2.plotly_chart(charts.tag_series(samples, tag), use_container_width=True)
            st.dataframe(
                rows.tail(200),
                column_config=column_help("EXTRACTS", "historian", rows.columns),
                use_container_width=True,
                hide_index=True,
            )
            explain(
                "What the quality colours mean",
                "**Good** samples are used. **Bad** samples (digital state *Bad Input*, no value) and "
                "**Questionable** ones are ignored by the data gate. Gaps are dropouts. A sensor that repeats the "
                "same value for 3 h is treated as *stuck* even if it says Good, because frozen sensors look healthy. "
                "On/off tags (unit `bool`) are exempt from that rule.",
            )
    with tabs[1]:
        _extract_header("tag_map")
        _show(zone.tag_map, "tag_map")
    with tabs[2]:
        _extract_header("well_tests")
        tests = zone.well_tests
        _show(tests, "well_tests")
        if not tests.empty:
            fig = px.scatter(
                tests,
                x="test_start_utc",
                y="q_gas_mmscfd",
                color="well_id",
                symbol="accepted",
                hover_data=["pwh_barg", "q_cond_bbld"],
            )
            fig.update_layout(title="Well tests over time (circles accepted, crosses rejected)", height=380)
            st.plotly_chart(fig, use_container_width=True)
        explain(
            "Why tests matter",
            "Each accepted test gives one measured point on a well's deliverability curve. NeO refits the curve to the latest accepted test (page 3). Confidence falls as the test ages.",
        )
    with tabs[3]:
        _extract_header("lab_samples")
        _show(zone.lab_samples, "lab_samples")
    with tabs[4]:
        _extract_header("events")
        events = zone.events
        _show(events, "events")
        if not events.empty:
            timeline = events.assign(end=events["end_utc"].fillna(events["start_utc"] + pd.Timedelta(days=30)))
            fig = px.timeline(
                timeline,
                x_start="start_utc",
                x_end="end",
                y="asset_id",
                color="type",
                hover_data=["capacity_removed", "note"],
            )
            fig.update_layout(title="Events: when capacity was lost", height=300)
            st.plotly_chart(fig, use_container_width=True)
        explain(
            "Planned vs unplanned",
            "Planned events (maintenance, derates) may be known before they start and count when NeO plans "
            "ahead. Unplanned ones (trips, well failures) only count once they have actually started before the "
            "cut-off, so a plan never 'knows' about a trip that hasn't happened yet.",
        )
    with tabs[5]:
        _extract_header("nominations")
        noms = zone.nominations
        _show(noms, "nominations")
        if not noms.empty:
            fig = px.line(noms, x="gas_day", y="qty_mmbtu_d", color="contract_id", markers=True)
            fig.update_layout(title="What each buyer asked for, per gas day", height=320)
            st.plotly_chart(fig, use_container_width=True)
    with tabs[6]:
        _extract_header("prices")
        _show(_read(zone_dir / "prices.csv"), "prices")
    with tabs[7]:
        _extract_header("third_party_schedule")
        _show(zone.third_party_schedule, "third_party_schedule")
        st.caption(
            "Days missing from this file are days the third party didn't declare; NeO then estimates from the measured flow and flags it."
        )
    with tabs[8]:
        _extract_header("terminal_plan")
        _show(zone.terminal_plan, "terminal_plan")
    with tabs[9]:
        _extract_header("dor_allocation")
        st.warning(
            "Verification only: NeO never uses allocated volumes as model inputs (they are back-calculated and lag a day)."
        )
        _show(zone.dor_allocation, "dor_allocation")
    with tabs[10]:
        _extract_header("registers")
        listing = pd.DataFrame(
            [{"file": str(p.relative_to(zone_dir)), "kB": round(p.stat().st_size / 1024, 1)} for p in files]
        )
        st.dataframe(listing, use_container_width=True, hide_index=True)


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def _tag_pattern(tag: str) -> str:
    if tag.startswith(("P1-", "P2-", "P3-")) and "." in tag:
        return f"<well>.{tag.split('.', 1)[1]}"
    for pattern in schema.NETWORK_TAGS.values():
        prefix = pattern.partition("{")[0]
        if pattern == tag or ("{" in pattern and tag.startswith(prefix) and tag.endswith(pattern.split("}")[-1])):
            return pattern
    return tag


def _tag_meaning(pattern: str) -> str:
    entry = doc("TAGS", None, pattern)
    return f"{entry.meaning} ({entry.unit})" if entry and entry.unit else (entry.meaning if entry else "")
