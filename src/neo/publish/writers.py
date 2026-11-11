"""Write the run's artefacts. The advisory is byte-identical for identical inputs; the
manifest carries timings and is the only file allowed to differ between reruns."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from neo.advise import AdviceBundle
from neo.domain.advisory import Advisory, DecisionRecord
from neo.explain.text import render_report


def write_bundle(bundle: AdviceBundle, out_dir: Path) -> Path:
    a = bundle.advisory
    run_dir = out_dir / a.gas_day.isoformat() / f"rev{a.revision}-{a.run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "advisory.json").write_text(a.model_dump_json(indent=2) + "\n")
    (run_dir / "advisory_report.md").write_text(render_report(a))
    (run_dir / "run_manifest.json").write_text(
        json.dumps(bundle.manifest, indent=2, sort_keys=True, default=str) + "\n"
    )
    _write_csv(run_dir / "nomination_draft.csv", bundle.nomination_draft)
    decision_log = run_dir / "decision_log.csv"
    if not decision_log.exists():
        _write_csv(decision_log, [], header=list(DecisionRecord.model_fields))
    return run_dir


def _write_csv(path: Path, rows: list[dict], *, header: list[str] | None = None) -> None:
    fields = header or (list(rows[0]) if rows else [])
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def export_schemas(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for model in (Advisory, DecisionRecord):
        path = out_dir / f"{model.__name__.lower()}.schema.json"
        path.write_text(json.dumps(model.model_json_schema(), indent=2) + "\n")
        written.append(path)
    return written
