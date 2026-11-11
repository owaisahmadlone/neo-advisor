"""Dictionary & glossary: every field, code and term in one searchable place."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from neo.ui.common import _dictionary, page_header


def _docs_frame(section: str, table: dict) -> pd.DataFrame:
    rows = []
    for group, fields in table.items():
        for name, d in fields.items():
            rows.append(
                {
                    "section": section,
                    "group": group,
                    "name": name,
                    "meaning": d.meaning,
                    "unit": d.unit,
                    "source": d.source,
                    "used for": d.used_for,
                }
            )
    return pd.DataFrame(rows)


def render() -> None:
    page_header(
        "Dictionary & glossary",
        "Every input column, historian tag, register field, internal state field, output field, code and term "
        "used anywhere in NeO, with its meaning, unit, source and how NeO uses it.",
        "The same text powers the hover help on every table in this UI. A test fails if any field or code in the "
        "code base is missing here, so nothing goes unexplained.",
    )
    dd = _dictionary()
    if dd is None:
        st.warning("The dictionary module is not available yet.")
        return
    query = st.text_input("Search everything", placeholder="e.g. ullage, shadow, PWH, shortfall, open_fully")
    extracts = pd.DataFrame(
        [
            {
                "section": "input file",
                "group": key,
                "name": col,
                "meaning": d.meaning,
                "unit": d.unit,
                "source": d.source,
                "used for": d.used_for,
            }
            for key, e in dd.EXTRACTS.items()
            for col, d in e.fields.items()
        ]
    )
    tags = pd.DataFrame(
        [
            {
                "section": "historian tag",
                "group": "tag",
                "name": k,
                "meaning": d.meaning,
                "unit": d.unit,
                "source": d.source,
                "used for": d.used_for,
            }
            for k, d in dd.TAGS.items()
        ]
    )
    everything = pd.concat(
        [
            extracts,
            tags,
            _docs_frame("register", dd.REGISTRY),
            _docs_frame("internal state", dd.STATE),
            _docs_frame("output", dd.OUTPUTS),
            _docs_frame("code", dd.CODES),
            pd.DataFrame(
                [
                    {
                        "section": "glossary",
                        "group": "term",
                        "name": k,
                        "meaning": v,
                        "unit": "",
                        "source": "",
                        "used for": "",
                    }
                    for k, v in dd.GLOSSARY.items()
                ]
            ),
        ],
        ignore_index=True,
    )
    if query:
        mask = everything.apply(lambda r: r.astype(str).str.contains(query, case=False, regex=False).any(), axis=1)
        st.caption(f"{int(mask.sum())} matches")
        st.dataframe(everything.loc[mask], use_container_width=True, hide_index=True)
    tabs = st.tabs(
        [
            "Pipeline stages",
            "Input files",
            "Historian tags",
            "Registers",
            "Internal state",
            "Outputs",
            "Codes",
            "Glossary",
        ]
    )
    with tabs[0]:
        st.dataframe(
            pd.DataFrame(
                [vars(s) if hasattr(s, "__dict__") else {f: getattr(s, f) for f in s.__slots__} for s in dd.PIPELINE]
            ),
            use_container_width=True,
            hide_index=True,
        )
    with tabs[1]:
        for key, e in dd.EXTRACTS.items():
            with st.expander(f"**{e.file}** — {e.title}"):
                st.markdown(f"Stands in for *{e.mimics}* · grain: {e.grain}  \n{e.why}")
                st.dataframe(
                    extracts.loc[extracts["group"] == key].drop(columns=["section", "group"]),
                    use_container_width=True,
                    hide_index=True,
                )
    with tabs[2]:
        st.dataframe(tags.drop(columns=["section", "group"]), use_container_width=True, hide_index=True)
    for tab, (section, table) in zip(
        tabs[3:7],
        [("register", dd.REGISTRY), ("internal state", dd.STATE), ("output", dd.OUTPUTS), ("code", dd.CODES)],
        strict=True,
    ):
        with tab:
            df = _docs_frame(section, table)
            for group in table:
                with st.expander(group):
                    st.dataframe(
                        df.loc[df["group"] == group].drop(columns=["section", "group"]),
                        use_container_width=True,
                        hide_index=True,
                    )
    with tabs[7]:
        st.dataframe(
            pd.DataFrame(
                [{"term": k, "meaning": v} for k, v in sorted(dd.GLOSSARY.items(), key=lambda kv: kv[0].lower())]
            ),
            use_container_width=True,
            hide_index=True,
        )
