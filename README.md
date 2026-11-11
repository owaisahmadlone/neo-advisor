# NeO v1 — network optimisation advisor

For each gas day, NeO ranks gas-condensate wells by their value to the whole network and recommends:
- well rates and chokes;
- routing;
- compressor loading;
- contract allocation.

It never breaks a hard limit. It is advisory only: planners approve, field operations implement.

- What is built, why, results and backlog: [docs/neo-workdoc.md](docs/neo-workdoc.md)
- Synthetic reference network and contracts: `configs/reference/`
- Scenario fixtures: `configs/fixtures/`

```bash
uv sync
uv run pytest
uv run neo advise --fixture configs/fixtures/trip.yaml --out data/out
uv run neo synth --days 30 --scenario normal --out data/landing
uv run neo advise --landing data/landing --gas-day 2026-11-20 --out data/out
uv run neo harness --scenarios normal,trip,co2cut,badtag --days 30
uv run neo schema --out schemas
```

Each advisory run writes these files to `data/out/<gas-day>/rev<n>-<run_id>/`:
- `advisory.json`
- `advisory_report.md`
- `nomination_draft.csv`
- `decision_log.csv`
- `run_manifest.json`
