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

## Explainer UI (internal)

```bash
uv sync --extra ui
uv run --extra ui neo ui
```

The UI opens at http://localhost:8501. Its pages, in order:
1. **Start here:** the problem and a guided tour.
2. **The network:** an interactive map, and the physics of every asset.
3. **Inputs:** every landing-zone file, with data quality shown.
4. **Data gate & calibration:** what NeO accepts, and what it learns from the data.
5. **Run an advisory:** a what-if lab plus real data, with every output visualised and explained.
6. **Evaluate:** a 30-day closed-loop test against incumbent practice, run live.
7. **Dictionary:** every field, code and term. Hover text on every table header comes from it.

The UI explains how NeO works internally, so it is not for clients.

Each advisory run writes these files to `data/out/<gas-day>/rev<n>-<run_id>/`:
- `advisory.json`
- `advisory_report.md`
- `nomination_draft.csv`
- `decision_log.csv`
- `run_manifest.json`
