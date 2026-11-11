"""Page 5: closed-loop evaluation against incumbent practice, run live or loaded from disk."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from neo.advise import AdviseConfig, advise
from neo.harness.metrics import DayRecord, PolicyDay, paired_uplift, violation_days
from neo.ui import charts
from neo.ui.common import (
    DATA_DIR,
    REGISTRY_DIR,
    RESULTS_DIR,
    code_meaning,
    explain,
    fresh_ui_dir,
    money,
    page_header,
    registry,
)

VIOLATION_DTYPES = {f"{p}_violations": str for p in ("advisor", "hold", "greedy")}

LOOP_DOT = """
digraph loop {
  rankdir=LR; node [shape=box, style="rounded,filled", fillcolor="#EEF3FB", fontname="Helvetica", fontsize=11];
  truth [label="Truth plant\\n(real network stand-in;\\ndiffers from NeO's model)", fillcolor="#FDEDEC"];
  files [label="Landing zone\\nhistorian, tests, events,\\nnominations… with noise"];
  neo [label="NeO advisory\\n(gate → solve → verify)", fillcolor="#E8F5EC"];
  op [label="Control room\\napply set-points; choke in\\npolicy order if a plant limit\\nbreaks or gas has no buyer", fillcolor="#FFF4E5"];
  base [label="Baselines on the SAME day\\nhold yesterday · greedy planner", fillcolor="#F3F4F6"];
  truth -> files [label="writes"]; files -> neo [label="reads at cut-off"]; neo -> op [label="advice"];
  op -> truth [label="next day"]; files -> base; base -> op [style=dashed];
}
"""


def _result_sets() -> dict[str, Path]:
    """Evaluation runs on disk: fresh ones under data/, plus the committed snapshot under results/."""
    sets = {}
    for base, label in ((DATA_DIR, "data"), (RESULTS_DIR, "results")):
        if not base.exists():
            continue
        for days_csv in base.rglob("days.csv"):
            root = days_csv.parent.parent
            sets[f"{label}/{root.relative_to(base)}"] = root
    return dict(sorted(sets.items(), key=lambda kv: kv[1].stat().st_mtime, reverse=True))


def _load(root: Path) -> dict[str, pd.DataFrame]:
    return {p.parent.name: pd.read_csv(p, dtype=VIOLATION_DTYPES).fillna("") for p in sorted(root.glob("*/days.csv"))}


def _records(df: pd.DataFrame) -> list[DayRecord]:
    def policy(row, name: str) -> PolicyDay:
        raw = row[f"{name}_violations"]
        return PolicyDay(
            row[f"{name}_value_usd"],
            row[f"{name}_shortfall_mmbtu"],
            tuple(v for v in str(raw).split(";") if v and v != "nan"),
        )

    return [
        DayRecord(
            row["scenario"],
            date.fromisoformat(row["gas_day"]),
            row["advisory_status"],
            row["advise_seconds"],
            {p: policy(row, p) for p in ("advisor", "hold", "greedy")},
        )
        for _, row in df.iterrows()
    ]


def _run_box() -> None:
    from neo.harness.loop import run_scenario
    from neo.synth import SCENARIOS

    with st.expander("▶ Run a new evaluation", expanded=not _result_sets()):
        c1, c2, c3 = st.columns(3)
        scenarios = c1.multiselect("Scenarios", sorted(SCENARIOS), default=["normal", "trip"])
        days = c2.number_input("Days per scenario", 3, 60, 10, help="30 days × 4 scenarios takes about 45 s.")
        seed = c3.number_input(
            "Seed", 0, 10_000, 7, help="Same seed = same truth plant and data, so results reproduce exactly."
        )
        if st.button("Run evaluation", type="primary", disabled=not scenarios):
            out = fresh_ui_dir(f"harness-s{seed}-{days}d")
            bar = st.progress(0.0, text="starting…")
            total = len(scenarios) * int(days)
            done = {"n": 0}

            def progress(scenario: str, day: int, n_days: int) -> None:
                done["n"] += 1
                bar.progress(done["n"] / total, text=f"{scenario}: day {day} of {n_days}")

            for scenario in scenarios:
                run_scenario(
                    REGISTRY_DIR,
                    out,
                    scenario,
                    days=int(days),
                    start=date(2026, 11, 1),
                    seed=int(seed),
                    progress=progress,
                )
            bar.empty()
            st.session_state["result_set"] = str(out.relative_to(DATA_DIR))
            st.rerun()


def render() -> None:
    page_header(
        "5 · Evaluate against incumbent practice",
        "NeO runs day after day on a simulated plant it has never seen the inside of. Each day the same plant is "
        "also run with two practices a planner might use today, under identical conditions, and the value, "
        "shortfall and limit breaches of all three are compared.",
        "This is how the method is proven before any client data exists, and how a shadow trial on real data "
        "would be judged later. A gain only counts if it holds across days and scenarios, inside a confidence "
        "interval.",
    )
    st.graphviz_chart(LOOP_DOT, use_container_width=True)
    c1, c2, c3 = st.columns(3)
    c1.markdown(f"**NeO (advisor)** — {code_meaning('policy', 'advisor')}")
    c2.markdown(f"**Hold** — {code_meaning('policy', 'hold')}")
    c3.markdown(f"**Greedy** — {code_meaning('policy', 'greedy')}")
    explain(
        "Why this comparison is fair",
        "- **Same day, same plant.** The truth plant has no hidden state, so each baseline is replayed on exactly "
        "the reservoir, events, weather and nominations NeO faced (paired comparison).\n"
        "- **Same protection.** Every policy gets the same control-room rule: if a plant limit breaks or gas "
        "has no buyer, choke wells in that policy's own order. NeO uses its merit order; the baselines use "
        "'highest CO₂ first'.\n"
        "- **No home advantage.** The truth plant's wells, pipes and condensate yield differ from NeO's model, "
        "and all NeO sees is noisy files.\n"
        "- **Uncertainty shown.** Uplift is the mean daily difference with a 90% bootstrap confidence interval "
        "over days.",
    )
    _run_box()
    sets = _result_sets()
    if not sets:
        return
    names = list(sets)
    chosen = st.session_state.get("result_set")
    name = st.selectbox("Results", names, index=names.index(chosen) if chosen in names else 0)
    results = _load(sets[name])

    rows = []
    for scenario, df in results.items():
        records = _records(df)
        hold, greedy = paired_uplift(records, "hold"), paired_uplift(records, "greedy")
        rows.append(
            {
                "scenario": scenario,
                "days": len(records),
                "blocked days": sum(r.advisory_status == "blocked" for r in records),
                "NeO violation-days": violation_days(records, "advisor"),
                "hold violation-days": violation_days(records, "hold"),
                "greedy violation-days": violation_days(records, "greedy"),
                "uplift vs hold (USD/d)": round(hold.mean_usd_d),
                "90% CI vs hold": f"{hold.ci90[0]:+,.0f} to {hold.ci90[1]:+,.0f}",
                "% vs hold": round(hold.mean_pct, 2),
                "uplift vs greedy (USD/d)": round(greedy.mean_usd_d),
                "90% CI vs greedy": f"{greedy.ci90[0]:+,.0f} to {greedy.ci90[1]:+,.0f}",
                "% vs greedy": round(greedy.mean_pct, 2),
                "days NeO beat greedy": f"{greedy.days_better}/{greedy.days}",
            }
        )
    st.subheader("Summary")
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    explain(
        "How to read the summary",
        "- **Uplift**: average extra value per day NeO earned over that practice. A **90% CI** wholly above zero "
        "means the gain is very unlikely to be luck.\n"
        "- **Violation-days**: days the true plant broke a hard limit under that policy. On a *blocked* day NeO "
        "issues no advice and operators hold yesterday's settings, so any breach that day belongs to 'hold'.\n"
        "- Published production-optimisation deployments report 1–3% gains; a result in that range is "
        "realistic. It proves the method on synthetic data and is not a client benefit claim.",
    )

    for scenario, df in results.items():
        with st.expander(
            f"Scenario **{scenario}**: {code_meaning('scenario', scenario)}", expanded=scenario == next(iter(results))
        ):
            st.plotly_chart(charts.harness_values(df), use_container_width=True)
            c1, c2 = st.columns(2)
            c1.plotly_chart(charts.harness_uplift(df), use_container_width=True)
            c2.plotly_chart(charts.harness_shortfall(df), use_container_width=True)
            bad = df.loc[
                (df["advisor_violations"] != "")
                | (df["hold_violations"] != "")
                | (df["greedy_violations"] != "")
                | (df["advisory_status"] == "blocked")
            ]
            if not bad.empty:
                st.markdown("**Days with a breach or a blocked advisory:**")
                st.dataframe(
                    bad[["gas_day", "advisory_status", "advisor_violations", "hold_violations", "greedy_violations"]],
                    use_container_width=True,
                    hide_index=True,
                )
            st.dataframe(df, use_container_width=True, hide_index=True)
            _drill_down(sets[name] / scenario / "landing", df, scenario)


def _drill_down(landing: Path, df: pd.DataFrame, scenario: str) -> None:
    if not landing.exists():
        return
    day = st.selectbox(f"Open one day's advisory ({scenario})", ["—", *df["gas_day"].tolist()], key=f"drill-{scenario}")
    if day == "—":
        return
    from neo.ingest.landing import LandingZone
    from neo.ingest.state_builder import build_state
    from neo.ui.pages.advisory import _merit, _summary, _wells

    with st.spinner("Rebuilding that day's advisory from the landing zone…"):
        state = build_state(LandingZone(landing), date.fromisoformat(day))
        bundle = advise(registry(), state, cfg=AdviseConfig(value_samples=0))
    st.caption(
        "Rebuilt from the files that existed at that day's cut-off. Same inputs and code give the same advice. "
        "Value shown here is NeO's own estimate; the table above is what the true plant delivered."
    )
    _summary(bundle)
    if bundle.advisory.status != "blocked":
        t1, t2 = st.tabs(["Well set-points", "Merit order"])
        with t1:
            _wells(bundle)
        with t2:
            _merit(bundle)
    row = df.loc[df["gas_day"] == day].iloc[0]
    st.markdown(
        f"On the true plant that day: NeO {money(row['advisor_value_usd'])}, hold {money(row['hold_value_usd'])}, "
        f"greedy {money(row['greedy_value_usd'])}."
    )
