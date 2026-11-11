"""The UI data dictionary documents every extract column, tag, field, code and stage NeO has.

Expected keys are read from the source (schema constants, model fields, Literal types,
regexes over the modules, models built at runtime), never typed here, so a new code fails
these tests until ``neo.ui.dictionary`` explains it.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
import re
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import get_args, get_type_hints

import pytest
from pydantic import BaseModel

import neo
from neo.domain import advisory, state
from neo.domain import registry as registry_models
from neo.domain.registry import Registry
from neo.domain.state import NetworkState
from neo.explain import merit
from neo.harness.metrics import POLICIES
from neo.ingest import schema
from neo.opt.build import Stage, StageCaps, build_plan_model
from neo.opt.solve import HARD_LIMIT_KINDS
from neo.synth import extracts as synth_extracts
from neo.synth.scenarios import SCENARIOS
from neo.synth.tags import TagKind
from neo.ui import dictionary as d
from neo.verify.forward import Violation

SRC = Path(neo.__file__).parent
REQUIRED_CATEGORIES = (
    "action",
    "advisory_status",
    "confidence",
    "merit_status",
    "well_flag",
    "status_reason",
    "data_flag",
    "limit",
    "row_kind",
    "value_component",
    "stage",
    "scenario",
    "policy",
    "severity",
)


def _source(relative: str) -> str:
    return (SRC / relative).read_text()


def _matches(pattern: str, *relatives: str) -> set[str]:
    found = {m for rel in relatives for m in re.findall(pattern, _source(rel))}
    assert found, f"pattern {pattern!r} found nothing in {relatives}: the regex has rotted"
    return found


def _missing(expected: set[str], documented: object) -> list[str]:
    return sorted(set(expected) - set(documented))


def _models(module: ModuleType) -> Iterator[type[BaseModel]]:
    for _, cls in inspect.getmembers(module, inspect.isclass):
        if issubclass(cls, BaseModel) and cls.__module__ == module.__name__ and cls.model_fields:
            yield cls


def _all_docs() -> Iterator[tuple[str, d.Doc]]:
    for key, extract in d.EXTRACTS.items():
        yield from ((f"EXTRACTS.{key}.{f}", doc) for f, doc in extract.fields.items())
    yield from ((f"TAGS.{t}", doc) for t, doc in d.TAGS.items())
    for name, section in {"REGISTRY": d.REGISTRY, "STATE": d.STATE, "OUTPUTS": d.OUTPUTS, "CODES": d.CODES}.items():
        for group, docs in section.items():
            yield from ((f"{name}.{group}.{k}", doc) for k, doc in docs.items())


# ----------------------------------------------------------------------------- inputs


@pytest.mark.parametrize("constant", sorted(n for n in vars(schema) if n.endswith("_COLUMNS")))
def test_every_extract_column_is_documented(constant: str) -> None:
    stem = constant.removesuffix("_COLUMNS").lower()
    keys = [k for k in d.EXTRACTS if k.startswith(stem)]
    assert len(keys) == 1, f"{constant}: expected one EXTRACTS entry starting with {stem!r}, got {keys}"
    assert not _missing(set(getattr(schema, constant)), d.EXTRACTS[keys[0]].fields)


def test_every_extract_file_and_register_is_documented() -> None:
    documented = {e.file for e in d.EXTRACTS.values()}
    written = {*synth_extracts.EXTRACTS, synth_extracts.TAG_MAP_FILE}
    assert not _missing(written, documented)
    assert not _missing(set(synth_extracts.REGISTER_FILES), d.EXTRACTS["registers"].fields)


def test_every_historian_tag_pattern_is_documented() -> None:
    expected = {*schema.NETWORK_TAGS.values(), *(f"<well>.{a}" for a in schema.WELL_ATTRIBUTES)}
    assert not _missing(expected, d.TAGS)


# ----------------------------------------------------------------------------- models


@pytest.mark.parametrize(
    ("model", "section"),
    [
        *(pytest.param(cls, d.REGISTRY, id=f"registry.{cls.__name__}") for cls in _models(registry_models)),
        *(pytest.param(cls, d.OUTPUTS, id=f"advisory.{cls.__name__}") for cls in _models(advisory)),
    ],
)
def test_every_model_field_is_documented(model: type[BaseModel], section: dict) -> None:
    assert model.__name__ in section, f"{model.__name__} has no section"
    assert not _missing(set(model.model_fields), section[model.__name__])


@pytest.mark.parametrize("cls", [state.NetworkState, state.WellState, state.OperatingPoint, state.DataFlag])
def test_every_state_field_is_documented(cls: type) -> None:
    assert not _missing({f.name for f in dataclasses.fields(cls)}, d.STATE[cls.__name__])


# ----------------------------------------------------------------------------- codes


def test_required_code_categories_exist() -> None:
    assert not _missing(set(REQUIRED_CATEGORIES), d.CODES)


@pytest.mark.parametrize(
    ("category", "values"),
    [
        pytest.param("action", get_args(advisory.Action), id="action"),
        pytest.param("advisory_status", get_args(advisory.Status), id="advisory_status"),
        pytest.param("confidence", get_args(advisory.Confidence), id="confidence"),
        pytest.param("merit_status", get_args(merit.Status), id="merit_status"),
        pytest.param("severity", get_args(state.Severity), id="severity.data_flag"),
        pytest.param("severity", get_args(get_type_hints(Violation)["severity"]), id="severity.verifier"),
        pytest.param("stage", get_args(Stage), id="stage"),
        pytest.param("quality", get_args(schema.Quality), id="quality"),
        pytest.param("event_type", schema.EVENT_TYPES, id="event_type"),
        pytest.param("decision", get_args(advisory.DecisionRecord.model_fields["decision"].annotation), id="decision"),
        pytest.param(
            "contract_kind", get_args(registry_models.Contract.model_fields["kind"].annotation), id="contract_kind"
        ),
        pytest.param("node_kind", get_args(registry_models.NodeKind), id="node_kind"),
        pytest.param("pressure_role", get_args(registry_models.PressureRole), id="pressure_role"),
        pytest.param("tag_kind", get_args(TagKind), id="tag_kind"),
        pytest.param("policy", POLICIES, id="policy"),
        pytest.param("scenario", tuple(SCENARIOS), id="scenario"),
    ],
)
def test_every_closed_set_value_is_documented(category: str, values: tuple[str, ...]) -> None:
    assert values, f"no values read for {category}"
    assert not _missing(set(values), d.CODES[category])


@pytest.mark.parametrize(
    ("category", "pattern", "files"),
    [
        pytest.param("limit", r'check\(\s*"(\w+)"', ("verify/forward.py",), id="limit"),
        pytest.param("status_reason", r'code="(\w+)"', ("advise.py",), id="status_reason"),
        pytest.param("well_flag", r'flags\.append\("(\w+)"\)', ("advise.py",), id="well_flag"),
        pytest.param("row_kind", r'kind="(\w+)"', ("opt/build.py",), id="row_kind.keyword"),
        pytest.param("row_kind", r'"kind": "(\w+)"', ("opt/build.py",), id="row_kind.tags_dict"),
        pytest.param("stage", r'stages\["(\w+)"\]', ("opt/solve.py",), id="stage.solve"),
    ],
)
def test_every_code_in_the_source_is_documented(category: str, pattern: str, files: tuple[str, ...]) -> None:
    assert not _missing(_matches(pattern, *files), d.CODES[category])


def test_every_data_flag_is_documented() -> None:
    modules = [str(p.relative_to(SRC)) for p in SRC.rglob("*.py") if "ui" not in p.relative_to(SRC).parts]
    assert not _missing(_matches(r'DataFlag\(\s*"(\w+)"', *modules), d.CODES["data_flag"])


def test_passed_through_reasons_are_documented() -> None:
    """Data flags become StatusReasons; verifier warnings other than the excluded ones do too."""
    tree = ast.parse(_source("verify/forward.py"))
    warnings = {
        call.args[0].value
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "check"
        and call.args
        and isinstance(call.args[0], ast.Constant)
        and any(k.arg == "severity" and getattr(k.value, "value", None) == "warning" for k in call.keywords)
    }
    excluded = _matches(r'v\.limit != "(\w+)"', "advise.py")
    assert warnings - excluded, "no verifier warning passes through: check the extraction"
    assert not _missing(warnings - excluded, d.CODES["status_reason"])
    assert not _missing(set(d.CODES["data_flag"]), d.CODES["status_reason"])


def test_built_model_row_kinds_and_value_parts_are_documented(registry: Registry, state: NetworkState) -> None:
    """Building every stage catches kinds the regexes cannot see (f-strings such as h2s_inlet)."""
    caps = StageCaps(sigma=0.0, weighted_shortfall=0.0)
    models = [build_plan_model(registry, state, stage=s, caps=caps) for s in get_args(Stage)]
    kinds = {row.kind for pm in models for row in pm.model.rows}
    parts = {name for pm in models for name in pm.value_parts}
    assert not _missing(kinds | HARD_LIMIT_KINDS | set(merit.REASON_LABELS) | set(merit.UNITS), d.CODES["row_kind"])
    assert not _missing(parts | set(merit.DIRECT_LABELS), d.CODES["value_component"])


# ----------------------------------------------------------------------------- quality of the text


def test_every_doc_has_a_meaning() -> None:
    empty = [where for where, doc in _all_docs() if not doc.meaning.strip()]
    assert not empty


def test_fragments_carry_no_final_period() -> None:
    """The UI writes 'Unit: {unit}.', 'Source: {source}.' and 'Used for: {used_for}.' around these."""
    dotted = [
        f"{where}.{name}"
        for where, doc in _all_docs()
        for name in ("unit", "source", "used_for")
        if getattr(doc, name).rstrip().endswith(".")
    ]
    assert not dotted


def test_extract_and_stage_docs_are_complete() -> None:
    blank = [
        f"{type(item).__name__}.{f.name}"
        for item in (*d.EXTRACTS.values(), *d.PIPELINE)
        for f in dataclasses.fields(item)
        if isinstance(getattr(item, f.name), str) and not getattr(item, f.name).strip()
    ]
    assert not blank


def test_pipeline_covers_the_stages_and_names_real_modules() -> None:
    keys = [s.key for s in d.PIPELINE]
    assert len(keys) >= 10
    assert len(keys) == len(set(keys))
    for stage in d.PIPELINE:
        for module in stage.module.split(","):
            importlib.import_module(module.strip())


def test_glossary_is_large_and_searchable() -> None:
    assert len(d.GLOSSARY) >= 70
    assert d.lookup("gsa") == d.GLOSSARY["GSA"]
    assert d.lookup("  Dual ") == d.GLOSSARY["shadow price / dual"]
    assert d.lookup("historian") == d.GLOSSARY["PI / historian"]
    assert d.lookup("no such term") is None
