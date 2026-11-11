"""Command line: ``neo advise | synth | harness | schema``."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

DEFAULT_REGISTRY = Path("configs/reference")


def _advise(args: argparse.Namespace) -> int:
    from neo.advise import AdviseConfig, advise
    from neo.domain.registry import Registry
    from neo.publish.writers import write_bundle

    cfg = AdviseConfig(value_samples=args.samples)
    if args.fixture:
        from neo.core.ids import file_hash
        from neo.domain.fixtures import FixtureSpec, state_from_registry

        registry = Registry.load(args.registry)
        spec = FixtureSpec.load(args.fixture)
        state = state_from_registry(registry, spec)
        hashes = {"fixture": file_hash(args.fixture)}
    else:
        from neo.ingest.landing import LandingZone
        from neo.ingest.state_builder import build_state

        if args.gas_day is None:
            raise SystemExit("--gas-day is required with --landing")
        zone = LandingZone(args.landing)
        registry = zone.registry
        state = build_state(zone, args.gas_day, revision=args.revision)
        hashes = zone.input_hashes(up_to=args.gas_day)
    bundle = advise(registry, state, cfg=cfg, input_hashes=hashes)
    run_dir = write_bundle(bundle, args.out)
    a = bundle.advisory
    print(json.dumps({"status": a.status, "run_id": a.run_id, "gas_day": a.gas_day.isoformat(), "out": str(run_dir)}))
    return 0 if a.status != "blocked" else 2


def _synth(args: argparse.Namespace) -> int:
    from neo.synth import generate

    generate(args.registry, args.out, start=args.start, days=args.days, scenario=args.scenario, seed=args.seed)
    print(json.dumps({"landing": str(args.out), "days": args.days, "scenario": args.scenario}))
    return 0


def _harness(args: argparse.Namespace) -> int:
    from neo.harness.loop import run_harness

    report = run_harness(
        args.registry, args.out, scenarios=args.scenarios.split(","), days=args.days, start=args.start, seed=args.seed
    )
    print(report)
    return 0


def _schema(args: argparse.Namespace) -> int:
    from neo.publish.writers import export_schemas

    for path in export_schemas(args.out):
        print(path)
    return 0


def _ui(args: argparse.Namespace) -> int:
    import subprocess

    app = Path(__file__).resolve().parent / "ui" / "app.py"
    command = [sys.executable, "-m", "streamlit", "run", str(app), "--server.port", str(args.port)]
    if args.headless:
        command += ["--server.headless", "true"]
    return subprocess.call(command)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="neo", description="NeO v1 gas network optimisation advisor")
    sub = parser.add_subparsers(dest="command", required=True)

    advise = sub.add_parser("advise", help="produce the advisory for one gas day")
    source = advise.add_mutually_exclusive_group(required=True)
    source.add_argument("--landing", type=Path, help="landing-zone directory")
    source.add_argument("--fixture", type=Path, help="fixture YAML (registry-derived state, no ingest)")
    advise.add_argument("--gas-day", type=date.fromisoformat)
    advise.add_argument("--revision", type=int, default=0)
    advise.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    advise.add_argument("--out", type=Path, default=Path("data/out"))
    advise.add_argument("--samples", type=int, default=60, help="Monte Carlo samples for the value band")
    advise.set_defaults(func=_advise)

    synth = sub.add_parser("synth", help="generate a synthetic landing zone (open loop)")
    synth.add_argument("--days", type=int, default=30)
    synth.add_argument("--scenario", default="normal")
    synth.add_argument("--start", type=date.fromisoformat, default=date(2026, 11, 1))
    synth.add_argument("--seed", type=int, default=7)
    synth.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    synth.add_argument("--out", type=Path, default=Path("data/landing"))
    synth.set_defaults(func=_synth)

    harness = sub.add_parser("harness", help="closed-loop evaluation against baselines")
    harness.add_argument("--scenarios", default="normal,trip,co2cut,badtag")
    harness.add_argument("--days", type=int, default=30)
    harness.add_argument("--start", type=date.fromisoformat, default=date(2026, 11, 1))
    harness.add_argument("--seed", type=int, default=7)
    harness.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    harness.add_argument("--out", type=Path, default=Path("data/harness"))
    harness.set_defaults(func=_harness)

    ui = sub.add_parser("ui", help="open the explainer UI (needs the ui extra: uv run --extra ui neo ui)")
    ui.add_argument("--port", type=int, default=8501)
    ui.add_argument("--headless", action="store_true", help="don't open a browser")
    ui.set_defaults(func=_ui)

    schema = sub.add_parser("schema", help="export JSON Schemas of the output contracts")
    schema.add_argument("--out", type=Path, default=Path("schemas"))
    schema.set_defaults(func=_schema)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
