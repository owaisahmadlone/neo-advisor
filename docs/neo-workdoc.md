# NeO v1 — work doc

Living record of what is built, why, what we measured, and what could make it better.
The forward plan this implements: `/Users/owaislone/.claude/plans/got-the-broader-overview-serialized-backus.md`.
Update this file at every build step: **built** (§2–3), **decided** (§4), **measured** (§5), **next** (§6).

---

## 1. What NeO v1 is

An advisory optimiser for an offshore gas-condensate network (Peninsular Malaysia shape: platform wells → shared compressor hub with third-party gas → two subsea trunk lines → onshore slug catcher → gas plant → GSA buyers and priority-of-supply excess; condensate to the terminal).

For each gas day it reads source-system-shaped extracts, gates them for quality, calibrates a steady-state physics model, and solves a lexicographic mixed-integer linear program:
1. hold step limits;
2. meet nominations in priority order;
3. maximise enterprise value.

A nonlinear forward simulation then checks every hard limit, and the advisory publishes rate set-points, a well merit order with reasons, the binding limits, the value at stake and a draft nomination. A planner approves, edits or rejects. Nothing writes to control systems.

Run it:
```bash
uv run neo advise --fixture configs/fixtures/trip.yaml --out data/out
uv run neo synth --days 30 --scenario normal --out data/landing
uv run neo advise --landing data/landing --gas-day 2026-11-20 --out data/out
uv run neo harness --scenarios normal,trip,co2cut,badtag --days 30
uv run pytest
```

---

## 2. System as built

| Component | Module | What it does | Key simplification | Evidence |
|---|---|---|---|---|
| Units, gas day, ids | `core/` | bara / MMscfd / MMBtu / bbl internally; gas day 06:00 Asia/Kuala_Lumpur; cut-off 11:00 local D−1; deterministic run ids | gas-day start hour is a config ⚠ | GFA day-ahead noon nomination (ST GP/ST/NO.10/2017) |
| Registers | `domain/registry.py`, `configs/reference/` | asset registry (topology, wells, pipes, compressor map, plant, limits), GSA register, PSC terms | limits embedded in the registry; separate limits register with MOC refs is backlog | — |
| Well physics | `physics/wells.py` | truth: backpressure IPR + average-T,z lift; advisor: lumped wellhead curve q = C(p_ws² − p_wh²)^n, choke as free pressure absorber; Turner minimum rate | constant CGR per day; full-open choke loss ignored (shown as "choke fully open") | Kosmidis et al. 2005; Turner et al. 1969; Fevang & Whitson 1996 |
| Pipe physics | `physics/pipes.py` | Δ(P²) = K q² + L/(1+βq): Weymouth friction + Flanigan hydrostatic liquid head; holdup inventory V·h/(1+βq) | single-phase gas equation with a liquid-head term, not a mechanistic multiphase model | Weymouth; Flanigan 1958; Beggs & Brill 1973 |
| Compressor | `physics/compressor.py` | polytropic head, power with ambient derate, stonewall and surge limits; fuel from shaft power | identical trains share load equally | Ríos-Mercado & Borraz-Sánchez 2015 |
| Quality | `physics/blend.py` | molar blending of CO2/H2S/Hg/GHV; plant shrink and CO2 removal | one plant, so blending is linear (no pooling) | ISO 6976; Li et al. 2011 (pooling, when needed) |
| Hydrates | `physics/hydrate.py` | T_h = a + b·ln P per stream; Hammerschmidt MEG dose | warm Malay Basin seabed means MEG rarely binds | Sloan & Koh 2008; Hammerschmidt 1934 |
| Linearisation | `opt/pwl.py` | chords under concave well curves; chords over convex pipe drops; compressor planes shifted under the map | all conservative; verified on dense grids in tests | — |
| Modelling layer | `opt/lp.py` | small matrix builder on HiGHS (highspy), rows tagged shared / well-specific | no Pyomo in v1 | — |
| Plan model | `opt/build.py` | MILP in u = P²: on/off, routing, train count; deliverability, back-pressure, compressor planes, ratio, Pd max, plant inlet, CO2 removal + sales spec, GHV band, H2S/Hg, liquids, slug sweep, MEG, platform water, contracts, elastic step limits | fuel = φ₀·m + φ₁·q at the worst ratio (over-estimate) | Gunnerud & Foss 2010; Codas et al. 2012; GassOpt (Rømo et al. 2009) |
| Solve | `opt/solve.py` | restore → deliver → value (MILP), fixed-integer LP, ranking LP; elastic diagnosis names conflicting limits | lexicographic priorities by weights 10^(1−priority) between GSAs | Selot et al. 2008; SPE 145723; O'Neill et al. 2005 |
| Merit order | `explain/merit.py` | unit value v_i = c_i − Σ_shared π_r·A_ri from the ranking LP (no step limits); off wells by forced-on re-solve; binding limits with shadow values | degenerate duals not yet reported as ±ε intervals | Jansen et al. 1997 (backlog) |
| Verifier | `verify/forward.py` | exact pressures back from set-point nodes with a natural trunk split; fixed-code check of every hard limit; implied chokes; JT hydrate warning. Same engine runs the truth plant in "respond" mode | steady state | — |
| Valuation | `verify/value.py` | priority allocation of sales energy, PSC netback, condensate, opex | — | — |
| Pipeline | `advise.py` | gate → solve → verify (tighten back-offs and retry, else block) → baseline → merit → P10/50/90 → advisory | value band samples deliverability only | — |
| Ingest | `ingest/` | landing-zone contract (`schema.py`), schema checks, data gate (bad, stale or stuck critical tags block the run), state builder | route inferred from manifold pressure; no route tags yet | PI AF quality states |
| Calibration | `calibrate/` | well C from the latest accepted test (p_ws from survey + decline); trunk K fitted from 14 days of historian data; WLS reconciliation of VFM rates to the hub meter | n and the liquid-head term stay at their priors | Narasimhan & Jordache 2000 |
| Outputs | `domain/advisory.py`, `publish/`, `explain/text.py` | `advisory.json` (versioned, JSON Schema), report, nomination draft, decision-log schema, internal run manifest | — | — |
| Synthetic plant | `synth/` | truth parameters away from the registry, two unmodelled effects, sensor noise, events, extracts | — | Bikmukhametov & Jäschke 2020 (error bands) |
| Harness | `harness/` | closed loop: truth → extracts → advisor → operator → truth; hold and greedy baselines replayed on the same true day; the same protection rule for every policy; scenarios normal, trip, co2cut, badtag | the operator accepts 100% in one step and guards on daily outcomes | Elgsæter et al. 2010 (what fraction of potential is realised) |

## 3. Data model as built

**Inputs (landing zone, `ingest/schema.py`).** These are all synthetic in v1; in deployment they come from the systems named here:

| Extract | Source system |
|---|---|
| `historian/*.parquet` | PI archive, UTC, with Good/Questionable/Bad quality |
| `well_tests.csv` | MPFM / test separator |
| `lab_samples.csv` | PVT lab |
| `events.csv` | FFN / ASOME / maintenance plan |
| `nominations.csv` | Nomination system |
| `prices.csv`, `third_party_schedule.csv`, `terminal_plan.csv` | Commercial, PAC data sharing, terminal management |
| `dor_allocation.csv` | DOR / Agnes; verification only, never a model input |
| `registers/` | Registers |

**Gate.**
- A critical tag blocks the run if it has no Good sample in the last 4 h before the cut-off, or none in the last 2 h, or it is flat for 3 h.
- Non-critical gaps are substituted and flagged.

**Outputs.**
- `advisory.json`: schema 1.0, client-visible.
- `advisory_report.md`.
- `nomination_draft.csv`.
- `decision_log.csv` (header only; the review UI fills it).
- `run_manifest.json`: internal; holds config, timings and verifier detail.

**Client-facing rule.** The advisory shows value, binding limits, confidence and reasons, as the brief asks. Solver details, back-off margins, penalty weights and calibration internals stay in the manifest. A test enforces this.

## 4. Decision log

| Date | Decision | Why | Rejected | Evidence |
|---|---|---|---|---|
| 2026-11-12 | Physics + MILP first; no ML in v1 | the hard parts are data, contracts and trust; a learned surrogate would add speed only when solve time demands it | GNN/PINN predictor in v1 | Foss et al. 2018; Liu et al. 2026 (simple baselines beat deep hybrids) |
| 2026-11-12 | Hard limits inside the optimiser, plus an independent verifier | shadow prices exist only when the limits are constraints in the model; the verifier catches linearisation bugs | drop/clip filter after prediction | O'Neill et al. 2005 |
| 2026-11-12 | Lexicographic objective (step limits → nominations → value) | both Malaysian precedents put meeting demand first; penalty rates are not yet known | single monetary objective (kept as the ranking LP) | Selot et al. 2008; SPE 145723 |
| 2026-11-12 | Rank wells on a separate LP with no step limits | duals under binding step limits hide network limits | ranking on the move-limited plan | design review |
| 2026-11-12 | Compressor capacity as planes in (P_s², P_d²) per train count | flat "machines × capacity" is wrong exactly on trip days | constant ullage | Ríos-Mercado & Borraz-Sánchez 2015 |
| 2026-11-12 | Elastic step limits | on a trip day the current point is already infeasible | hard step limits (would block exactly when advice is needed) | design review |
| 2026-11-12 | highspy directly, no Pyomo | ~200 rows; explanation needs tagged rows and duals | Pyomo | — |
| 2026-11-12 | Truth plant ≠ advisor model | avoid the inverse crime | testing on the advisor's own model | Kaipio & Somersalo 2007 |
| 2026-11-12 | Show "choke fully open" instead of >100% | at-capacity wells have a near-zero choke drop; the lumped curve ignores full-open choke loss | modelling choke loss in deliverability (backlog) | — |
| 2026-11-12 | No deliverability back-off (`deliverability_k = 0`); back-offs only on hard limits | rate targets are choke-controlled, so a well that cannot reach its target just under-delivers (a value risk, not a safety risk); the back-off only lost value (harness: −0.8% vs greedy) | uncertainty-discounted deliverability | harness, §5 |
| 2026-11-12 | Wells limited only by their own deliverability get "open fully"; the advisory carries a choke-back order (lowest unit value first) | a rate cap on such wells leaves gas whenever the model under-estimates them; network limits are protected intraday by choking in merit order — the order is the point of the ranking | rate targets for every well | harness, §5 |
| 2026-11-12 | Count the CO2 that leaves with compressor fuel (linearised at today's mix) | ignoring it overstated the CO2 load by ~0.7 MMscfd, about 3–4 MMscfd of P1 gas forgone; the verifier re-checks the exact blend | strictly conservative CO2 load | harness, §5 |
| 2026-11-12 | Digital tags (uom "bool") are exempt from the stuck-sensor rule | train RUN and well STATUS legitimately hold one value for days; the rule blocked every run | one rule for all tags | synth agent's integration run |
| 2026-11-12 | Unplanned events (trips, well failures) count only once started before the cut-off; planned ones may be known ahead | otherwise an open-loop landing zone leaks future trips into earlier plans | use every event in the file | synth agent's integration run |
| 2026-11-12 | The "hold" comparison in the advisory uses the plant's response (respond mode), and flags when holding would breach a limit | on a trip day, holding valued an overloaded plan as if it delivered | check-mode valuation of the baseline | trip-day report |
| 2026-11-12 | The harness gives every policy the same protection rule (choke in its own order until plant limits hold and all gas has a buyer) | the comparison is fair only if the baselines get the same control-room behaviour | guard for the advisor only | — |

## 5. Results log

### 2026-11-12 — closed-loop harness, v0.1.0 (`uv run neo harness --days 30`, seed 7)

**Setup.**
- 30 gas days per scenario on the synthetic truth plant.
- The advisor drives the loop. Each day the baselines are replayed on the same true conditions from the same starting rates, so every comparison is paired.
- One rule protects the plant for every policy: choke in the policy's own order until plant limits hold and all gas has a buyer.

| Scenario | Blocked days | Advisor violation-days | Hold | Greedy | Uplift vs hold (USD/d, 90% CI) | Uplift vs greedy (USD/d, 90% CI) | Mean shortfall advisor / hold / greedy (MMBtu/d) | Advise time |
|---|---|---|---|---|---|---|---|---|
| normal | 0 | 0 | 0 | 0 | +23,822 (+17,533 to +32,736), +1.4% | +20,018 (+10,777 to +31,020), +1.2% | 27,189 / 31,867 / 31,262 | 0.19 s |
| trip | 0 | 0 | 1 | 0 | +25,281 (+16,589 to +35,338), +1.5% | +29,209 (+16,083 to +44,442), +1.7% | 30,052 / 35,037 / 35,818 | 0.19 s |
| co2cut | 0 | 0 | 0 | 0 | +26,968 (+19,196 to +36,642), +1.6% | +24,386 (+14,177 to +36,211), +1.4% | 30,126 / 35,543 / 34,861 | 0.19 s |
| badtag | 1 | 1* | 2 | 2 | +24,415 (+17,485 to +33,610), +1.4% | +36,526 (+18,163 to +58,935), +2.2% | 27,873 / 32,963 / 34,916 | 0.19 s |

\* On the day the data gate blocked the advisory (bad critical tag, as designed), operators held yesterday's settings, and that fallback breached a compressor limit. With an advisory in force the advisor had zero violation-days.

**Reading it.**
- The gain is 1.2–2.2%, consistently positive, and every confidence interval is above zero. That sits inside published deployment results (BP 1–3%; ~2% in the RTO survey).
- It is a method proof on synthetic data, not a benefit claim for the client.
- The 5–10% NeO headline should not be quoted from this.

**How we got here (same session).** The first harness run lost to the greedy heuristic. Root cause: conservatism that bought no safety.
1. A deliverability back-off of 3–6%. For choke-controlled rate targets, under-delivery is a value risk, not a safety risk, so the back-off only gave value away.
2. The CO2 burned as compressor fuel was ignored, overstating the plant's CO2 load by about 0.7 MMscfd.
3. Rate targets on wells limited only by their own deliverability left gas behind whenever the model under-estimated them.

Fixes, in order, and the result against greedy after each:
- −0.8% at the start;
- drop the deliverability back-off and count the fuel CO2: +0.6%;
- "open fully" set-points plus a choke-back order: +2.3% (4-day smoke runs).

See the decision log.

### 2026-11-12 — fixture runs (registry-derived state, no ingest)
- **Normal:** CO2 removal binds (19.6/19.6 MMscfd). The advisor chokes the highest-CO2 P1 wells and opens P2/P3, worth +$142k/d against holding the fixture's 85%-of-design rates.
- **Trip:** raises suction to about 35 bara (more capacity per remaining train) and keeps Buyer 1 whole; Buyer 2 absorbs the shortfall.
- **Solve time:** all fixtures solve in about 0.08 s (five stages). The full advisory, including merit-order re-solves and the value band, takes 0.6 s.
- **Correctness checks:**
  - the MILP matches exhaustive enumeration on a 5-well sub-network;
  - the dual-based unit value matches a finite-difference re-solve within 15%;
  - the optimiser's pressures always bound the physical ones.

## 6. Improvement backlog

| Item | Component | What it fixes | Evidence | Trigger to do it | Effort |
|---|---|---|---|---|---|
| ±ε dual intervals and ties | explain | degenerate duals can mislead | Jansen et al. 1997 | any well with interval spanning 0 in a client run | S |
| Full-open choke loss in deliverability | physics/opt | wells at capacity slightly over-promised | nodal analysis | harness shows clipped at-max wells | M |
| Ratio-dependent fuel (plane in q, head) | opt | fuel cost of lowering suction is understated | — | suction choice sensitive to fuel in harness | S |
| Pooling formulation | opt | a second plant or buyer-specific spec makes blending bilinear | Alfaki & Haugland 2013; Gupte et al. 2017 | second plant or spec split in client topology | L |
| Mechanistic multiphase trunks | physics | Flanigan term misfits at high liquid loading | Beggs & Brill 1973 | trunk calibration residual > 3% | M |
| Modifier adaptation | calibrate/opt | persistent plant–model bias | Marchetti et al. 2009 | predicted-vs-actual bias > 2% for 2 weeks | M |
| Robust / scenario well potential | opt | value band only covers deliverability | Krishnamoorthy et al. 2016 | P10 frequently below baseline | M |
| Monetary-penalty objective mode | opt | when Commercial supplies penalty terms | — | Commercial sign-off | S |
| Multi-day horizon | opt | take-or-pay, tank inventory, linepack | — | monthly balances matter to client | L |
| Limits register with MOC refs | domain | ownership and change control of limits | IEC 62443 / MOC practice | client limit owners named | S |
| Route position tags | ingest | route currently inferred from pressure | — | client tag list | S |
| Grey-box VFM | calibrate | per-well rates between tests | Bikmukhametov & Jäschke 2020 | test residuals beyond band | M |
| Learned surrogate (PI-GNN) | opt | solve time at client scale | arXiv 2607.13610 | daily solve > 60 s at real size | L |
| Ask NeO (explain-only LLM layer) | UI | conversational what-ifs | arXiv 2026 LLM-agent anomaly paper | planners ask for it after pilot | M |
| Platform decomposition | opt | scale beyond ~100 wells | Gunnerud & Foss 2010 | MILP time grows past budget | M |
| Harness: partial acceptance, ramps, drift, DOR lag | harness | more realistic operator and data | SPE 145723 (shadow trial) | before the client shadow trial | M |
| Intraday re-rank and revisions | pipeline | renominations and events within the day | — | after the daily plan is trusted | M |
| `neo verify` against DOR | pipeline | benefit register | — | first live days | S |
| Continuous well calibration from fully-open operating points (CHOKE ≥ 95% → point on the curve) | calibrate | deliverability drifts between monthly tests | Shell PU-RTM continuous VFM (SPE 145723) | test-age-driven error visible in predicted-vs-actual | M |
| Git commit in the run id | core | today the run id changes only with inputs, config and package version, not with code edits | — | before the first shadow trial | S |
| Harness operator realism: intraday feedback on the plant CO2 analyser, partial acceptance, ramp limits | harness | the guard acts on daily outcomes, not within the day | SPE 145723 | before quoting harness numbers to the client | M |
| Suppress the HiGHS banner on stdout | cli | the banner prints once per process | — | when the CLI is wrapped by a scheduler | S |
| Choke-back order per binding limit (CO2 order ≠ compression order) | explain | one order today, by unit value | — | a client day with two different binding limits | S |

## 7. Open questions for the client
- Do calibrated GAP/IPM/iCON network models exist? What does PrO compute today? (PETRONAS published an LP of the Peninsular fields in 2008.)
- Gas-day start hour; nomination and renomination windows on PGU.
- PGU sales-gas spec (GHV band, CO2 max), and where it is measured.
- Is priority of supply strictly contractual, or price-driven?
- PSC netback factors per block; which "enterprise" value is maximised.
- Is PAC (third-party) data shared, and on what cadence?
- Who owns each limit, and what is the MOC process?
- Is the network usually demand-limited or supply-limited?
- Real reservoir pressures, CGR ranges, trunk sizes, arrival pressures (all ⚠ assumptions in `configs/reference`).
