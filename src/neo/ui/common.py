"""Shared helpers for the explainer UI: paths, cached loaders, dictionary lookups, layout bits."""

from __future__ import annotations

import shutil
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from neo.domain.registry import Registry

ROOT = Path(__file__).resolve().parents[3]
REGISTRY_DIR = ROOT / "configs" / "reference"
FIXTURE_DIR = ROOT / "configs" / "fixtures"
DATA_DIR = ROOT / "data"
UI_DATA_DIR = DATA_DIR / "ui"
RESULTS_DIR = ROOT / "results"  # small committed snapshot of evaluation results (no landing zones)
UI_MARKER = ".neo-ui"

COLORS = {
    "advisor": "#2E6FD8",
    "hold": "#9AA4B2",
    "greedy": "#E08A2E",
    "ok": "#2E9D5B",
    "warn": "#E0A12E",
    "bad": "#D64545",
    "gas": "#2E6FD8",
    "condensate": "#8A5CD6",
    "water": "#2EA8C9",
    "P1": "#D64545",
    "P2": "#2E9D5B",
    "P3": "#E08A2E",
    "neutral": "#5B6575",
}


# ---------------------------------------------------------------- dictionary
def _dictionary():
    try:
        from neo.ui import dictionary
    except ImportError:
        return None
    return dictionary


def doc(section: str, group: str | None, key: str):
    """Look up a dictionary entry; ``section`` is EXTRACTS, TAGS, REGISTRY, STATE, OUTPUTS or CODES."""
    dd = _dictionary()
    if dd is None:
        return None
    table = getattr(dd, section, {})
    if section == "EXTRACTS":
        extract = table.get(group)
        return extract.fields.get(key) if extract else None
    if group is None:
        return table.get(key)
    return table.get(group, {}).get(key)


def meaning(section: str, group: str | None, key: str, default: str = "") -> str:
    entry = doc(section, group, key)
    if entry is None:
        return default
    unit = f" [{entry.unit}]" if getattr(entry, "unit", "") else ""
    return f"{entry.meaning}{unit}"


def extract_doc(key: str):
    dd = _dictionary()
    return dd.EXTRACTS.get(key) if dd else None


def column_help(section: str, group: str | None, columns: Iterable[str]) -> dict[str, Any]:
    """Streamlit column config whose header tooltips carry the dictionary meaning."""
    config = {}
    for column in columns:
        entry = doc(section, group, column)
        if entry is not None:
            parts = [entry.meaning]
            if entry.unit:
                parts.append(f"Unit: {entry.unit}.")
            if entry.source:
                parts.append(f"Source: {entry.source}.")
            if entry.used_for:
                parts.append(f"Used for: {entry.used_for}.")
            config[column] = st.column_config.Column(help=" ".join(parts))
    return config


def code_meaning(category: str, code: str) -> str:
    return meaning("CODES", category, code, default=code.replace("_", " "))


def glossary_term(term: str) -> str:
    dd = _dictionary()
    if dd is None:
        return ""
    return dd.GLOSSARY.get(term) or (dd.lookup(term) or "")


# ------------------------------------------------------------------- loaders
@st.cache_resource(show_spinner=False)
def registry() -> Registry:
    return Registry.load(REGISTRY_DIR)


def landing_zones() -> dict[str, Path]:
    """Every landing zone under data/ (a directory holding tag_map.csv), newest first."""
    if not DATA_DIR.exists():
        return {}
    zones = sorted((p.parent for p in DATA_DIR.rglob("tag_map.csv")), key=lambda p: p.stat().st_mtime, reverse=True)
    return {str(z.relative_to(DATA_DIR)): z for z in zones}


def zone_selector(*, label: str = "Landing zone") -> Path | None:
    """Shared landing-zone choice (kept in session state so every page uses the same one)."""
    zones = landing_zones()
    if not zones:
        st.info("No landing zone yet. Generate one on page 2 (Inputs) or run the evaluation on page 5.")
        return None
    names = list(zones)
    current = st.session_state.get("zone_name")
    index = names.index(current) if current in names else 0
    name = st.selectbox(label, names, index=index, help="Folders under data/ that contain a full set of extracts.")
    st.session_state["zone_name"] = name
    return zones[name]


def fixtures() -> dict[str, Path]:
    return {p.stem: p for p in sorted(FIXTURE_DIR.glob("*.yaml"))}


def fresh_ui_dir(name: str) -> Path:
    """A clean directory under data/ui that only the UI writes to."""
    path = UI_DATA_DIR / name
    if path.exists():
        if not (path / UI_MARKER).exists():
            raise FileExistsError(f"{path} was not created by the UI")
        shutil.rmtree(path)
    path.mkdir(parents=True)
    (path / UI_MARKER).write_text("created by the NeO explainer UI\n")
    return path


def available_gas_days(zone_dir: Path) -> list:
    """Gas days that can be advised: those whose cut-off falls inside the historian coverage."""
    files = sorted((zone_dir / "historian").glob("*.parquet"))
    if len(files) < 2:
        return []
    first = pd.Timestamp(files[0].stem).date()
    last = pd.Timestamp(files[-1].stem).date()
    calendar = registry().network.calendar
    days = []
    for offset in range((last - first).days + 2):
        day = first + pd.Timedelta(days=offset)
        cutoff = calendar.nomination_cutoff_utc(day)
        if (
            pd.Timestamp(files[0].stem, tz="UTC") + pd.Timedelta(hours=6)
            <= cutoff
            <= pd.Timestamp(files[-1].stem, tz="UTC") + pd.Timedelta(days=1)
        ):
            days.append(day)
    return days


# -------------------------------------------------------------------- layout
def page_header(title: str, what: str, why: str) -> None:
    st.title(title)
    st.markdown(f"**What you're looking at.** {what}")
    st.markdown(f"**Why it matters.** {why}")
    st.divider()


def explain(label: str, body: str, *, expanded: bool = False) -> None:
    with st.expander(f"ⓘ {label}", expanded=expanded):
        st.markdown(body)


def money(value: float, *, signed: bool = False) -> str:
    if signed:
        return f"{'+' if value >= 0 else '−'}${abs(value):,.0f}"
    return f"${value:,.0f}"


def frame(rows: Iterable[Mapping[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


def internal_banner() -> None:
    st.sidebar.warning(
        "**Internal tool.** This explainer shows how NeO works inside (solver stages, shadow prices, "
        "back-offs). Do not show it to clients; client-facing artefacts are the advisory JSON and report.",
        icon="🔒",
    )
