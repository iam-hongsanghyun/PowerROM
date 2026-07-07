# PowerROM — Development Roadmap

> **PowerROM** is a reduced-order (ROM) energy-system **screening** model — not a full
> LP/MILP optimizer like PyPSA. It computes system LCOE, emissions, curtailment, and
> storage need for a user-chosen generation mix, per country, from an hourly (8760h)
> merit-order dispatch run as a jittered multi-year ensemble (p10/median/p90).
>
> *Last updated: 2026-07-07 · PLANiT Institute*

---

## Product vision

A **simple GUI for policy stakeholders** to understand *"what happens based on different
features"* — carbon prices, renewable targets, coal phase-outs, storage costs, electrification —
without needing to run or interpret a full capacity-expansion model.

The tool must stay **legible and defensible**: every number traceable, every lever shown as
its own line in the cost stack, every model limitation disclosed.

### Architectural trajectory

```
PROFILE-based   ──▶   PARAMETER-based   ──▶   POLICY LEVERS   ──▶   SECTOR COUPLING
   (now)               (next)                  (overlay)             (demand-side)
```

1. **PROFILE-based (now)** — behaviour is baked into 8760h country profiles + a fitted
   `eta_func`. The user tunes capacities, carbon price, demand scale, battery cost.
2. **PARAMETER-based (next)** — expose the levers currently locked in the profile JSON
   (lifetimes, fuel prices, discount rate, resource quality, retirement) as first-class
   user inputs, so scenarios are built from parameters rather than hand-edited profiles.
3. **POLICY LEVERS** — a lever overlay (RPS, phase-out, ITC/PTC, FIT, import tariff, DSM)
   layered on top of the parameter engine, each following the `carbon_price` precedent.
4. **SECTOR COUPLING** — demand-side blocks (EV, heat, hydrogen, industry, power-to-gas)
   added to the hourly demand profile before dispatch.

### Legend

- **Priority** — `P1` (do first, unblocks or high stakeholder value), `P2` (next), `P3` (later / nice-to-have).
- **Effort** — `S` (< 1 day), `M` (1–3 days), `L` (> 3 days / multi-PR).

### Design principle (applies to every lever & coupling task)

Follow the `carbon_price` precedent exactly (`backend/core/lcoe_engine.py:151`):

1. Add a **bounded scalar / small dict** on `CalculateRequest` (`backend/models/schemas.py`).
2. Implement as a **pure function of existing per-generator fields** in
   `_generator_breakdown` / `_ess_metrics` — a cost adder, a share check, or a pattern
   multiplier. No new solver.
3. Emit a **new line item in `stack_components`** so the UI shows the lever's cost/benefit transparently.
4. Add a **`docs/ALGORITHM.md` entry** (LaTeX + ASCII) and a **regression test** against a
   hand-computed baseline (per `CLAUDE.md` numerical-correctness convention).

---

## Phase 0 — Hardening & current-state debt (foundation)

Close the gaps that make the *current* tool untrustworthy or inconsistent before building on top.

### Epic 0.1 — Custom-parameter plumbing consistency

- **`P1` / `M`** — Pass `customProfile` / `custom_params` through **`ProfileAnalysis.tsx`**
  sensitivity calls (carbon-price and battery-cost scenario fetches). Today parameter
  edits are invisible in the sensitivity charts (still-valid debt #2).
- **`P1` / `S`** — Pass `custom_params` through **`GeneratorMixPlotter.tsx`** `generateGrid`
  so the mix explorer respects user cost edits (still-valid debt #3).
- **`P1` / `S`** — Validate that `shares` / `capacities_gw` keys match the profile's generator
  list; fail loudly (not silently) on extra/missing keys (still-valid debt #6). Add to the
  `CalculateRequest` validator alongside the existing `capacities_gw` checks
  (`backend/models/schemas.py:53`).

### Epic 0.2 — Surface hidden physics

- **`P1` / `M`** — Display `backup_flexibility` (already computed at `lcoe_engine.py:168`,
  returned but never rendered) in the UI so users see *why* nuclear-heavy portfolios curtail
  VRE (still-valid debt #4).
- **`P2` / `M`** — Break out **long-duration ESS** as its own bar in the cost-stack chart
  (`CostBreakdownChart`), not just the status bar, so the cost penalty of 80%+ VRE
  pathways is visible (still-valid debt #5).

### Epic 0.3 — Test & governance baseline

- **`P1` / `L`** — Backend **automated test suite** for the dispatch + LCOE engine.
  `backend/tests/test_dispatch_engine.py` exists as a seed; extend to `_generator_breakdown`,
  `_ess_metrics`, `_size_storage_from_pattern`, and the ensemble aggregation. Regression
  tests against hand-computed baselines with explicit `rtol` / `atol` (roadmap I2 — currently
  the single highest-risk item for policy / publication use).
- **`P2` / `S`** — Profile **versioning + changelog** on country JSON (roadmap I1); stamp a
  version + provenance block into `KR/AU/JP.json`.

### Epic 0.4 — Dead-code cleanup

- **`P3` / `S`** — Decide the fate of `cf_eff_func` / `curtailment_func` /
  `integration_cost_func`: they are retained in profile JSON for backward compat but never
  called under hourly dispatch. Either delete or explicitly quarantine behind a documented
  "legacy" flag. (`integration` is hardwired to `0.0` at `lcoe_engine.py:162,328,345` —
  see Epic 3.6 for the re-enable path.)

---

## Phase 1 — Stakeholder-friendly GUI & feature understanding

Make the *existing* model legible to non-technical policy staff. No new physics.

### Epic 1.1 — Plain-language reframing

- **`P1` / `M`** — Plain-English labels + tooltips for jargon: "VRE share", `ess_requirement_gwh`,
  `emission_intensity`, `cf_base`, `variability_factor`. One glossary source, reused across
  `ControlPanel.tsx`, `ShareSliders.tsx`, `ProfileAnalysis.tsx`.
- **`P2` / `S`** — Explain the **merit-order drag** (`ShareSliders.tsx`): help text on what
  reordering means and when a policy maker would touch it. Flag the documented limitation
  that merit order is user-set, not cost-optimized (so carbon price does not reshuffle dispatch).
- **`P2` / `S`** — Contextualize **curtailment**: what drives it, when it's a problem, what
  reduces it (ties to `backup_flexibility` from Epic 0.2).

### Epic 1.2 — Onboarding & progressive disclosure

- **`P1` / `M`** — **Guided onboarding wizard** (roadmap D1) — a 3–4 step first-run flow that
  sets country, a policy question, and a starting mix.
- **`P2` / `M`** — Restructure the **Advanced** section of `ControlPanel.tsx` (dispatch mode,
  weather years, ensemble config, EV): promote policy-relevant controls, keep true expert
  knobs (jitter sigma, seed) collapsed. Add a one-line explainer for the p10/median/p90 bands.

### Epic 1.3 — Scenario management

- **`P1` / `L`** — **Scenario save / label / side-by-side compare** (roadmap A2). Scenario
  slots holding the full `CalculateRequest` state; render two/three results side by side.
- **`P1` / `M`** — **Shareable URL** encoding full scenario state (roadmap B2); foundation
  for reproducible policy memos.
- **`P2` / `M`** — **"Current government policy" baseline** marker (roadmap A1): pin a
  reference scenario, show deltas against it.

### Epic 1.4 — Policy-communication outputs

- **`P2` / `M`** — **Export to PDF / PowerPoint-ready image** (roadmap B1) for cabinet memos.
- **`P2` / `S`** — **Data provenance on every number** (roadmap B3) and a **model-limitations
  disclosure** panel (roadmap B4) — carbon price does not reshuffle dispatch; single-country
  islanded grids; screening not optimization.
- **`P3` / `M`** — **Capacity mix output in GW** surfaced in the UI (roadmap C4; already
  computed in dispatch via `capacities_gw`).

### Epic 1.5 — Policy-impact dashboard

- **`P2` / `L`** — A policy-framed results view: *"If you enact these policies, cost changes
  by $X/MWh, emissions drop Y%, storage grows to Z GWh"* — reorganizing existing metrics
  around the decision, not the technical breakdown.

---

## Phase 2 — Profile → Parameter engine

Expose the levers currently locked inside the country profile JSON as first-class user inputs.
This is the architectural pivot the vision calls for. Most items add fields to `custom_params`
(already threaded via `CalculateRequest.custom_params`, `schemas.py:46`).

### Epic 2.1 — Demand parameterization

- **`P1` / `M`** — `demand_growth_rate_pct_yr` + `annual_demand_twh` (already exposed) as a
  trend, not just a single scalar. Hook in `_calculate_system_lcoe_dispatch` before dispatch.
- **`P2` / `M`** — Load-shape parameters: `peak_load_ratio`, `industrial_load_fraction`
  applied to `demand_norm` in `hourly_profiles.py` (currently a fixed synthesized shape).

### Epic 2.2 — Resource-quality parameterization

- **`P2` / `M`** — `solar_cf_base_override`, `wind_cf_base_override` as intuitive inputs
  (editable today only via raw `custom_params`), plus
  `renewable_resource_degradation_rate_pct_yr`.

### Epic 2.3 — Cost & finance parameterization

- **`P1` / `M`** — Per-generator `lifetime_yr` and `discount_rate_override` (today a
  country-wide scalar) exposed to model early retirement / lifetime extension and WACC changes.
- **`P2` / `M`** — `fuel_price_override_usd_mmbtu` and `emission_factor_override_tco2_mwh`
  (today profile-locked) to model fuel escalation / carbon-intensity shift.
- **`P2` / `M`** — Storage overrides: `ess_capex_usd_kwh_override`, `ess_duration_hr_override`
  (battery-cost slider exists; duration + others are profile-locked).

### Epic 2.4 — Capacity feasibility bounds

- **`P2` / `L`** — `capacity_min_gw` / `capacity_max_gw` per generator feeding the existing
  `fixed_capacities` override path in `dispatch_engine.py:44,60,71,81`. Prerequisite for the
  phase-out and build-out policy levers (Epic 3.2 / 3.9).

### Epic 2.5 — Time-horizon pathway

- **`P2` / `L`** — Wrapper loop that runs the existing single-year ROM at each milestone year
  with interpolated inputs, charting LCOE / emissions / curtailment / ESS over time (roadmap C2).
  Requires **CAPEX learning curves** (technology cost trajectories by year). Foundation for
  multi-year retirement pathways (Epic 3.9), escalating carbon price (Epic 3.4), and cost-forecast controls.

### Epic 2.6 — Country library expansion

- **`P2` / `L`** — Additional country profiles, priority **DE, GB, IN, US, CN, FR** (roadmap
  C5). ~1 week each to source + validate. Enables multi-country comparison (roadmap C1).
- **`P3` / `M`** — **Energy-security metrics** (roadmap C3): import-dependency index, fuel cost
  as % of system cost. Build *together* with the import-tariff lever (Epic 3.5) — shared
  `import_dependency_fraction` field on the country JSON.

---

## Phase 3 — Policy levers (overlay on the parameter engine)

Each lever = bounded scalar/dict → pure function of existing fields → new `stack_components`
line → `ALGORITHM.md` entry + regression test. Sequenced per the research recommendation
(highest value / lowest effort first).

### Epic 3.1 — RPS / renewable mandate  `P1` / `S`  *(ship first)*

Share-floor **pass/fail badge** (traffic-light, matching roadmap A4) comparing chosen
`vre_share` (already computed, `lcoe_engine.py:304`) against a `rps_target_share` +
`rps_target_year`. Optional `rps_penalty_usd_mwh` (alternative-compliance / REC price) as a
shortfall cost adder into `system_lcoe`. Config toggle for whether nuclear/hydro count toward
the target (`VRE_GENERATORS` set exists at `lcoe_engine.py:13`; RPS definitions vary by jurisdiction).

### Epic 3.2 — Single-year capacity targets  `P1` / `S`  *(ship first)*

Floor/ceiling a generator's capacity via the **existing** `capacities_gw` override
(`dispatch_engine.py:60`). `capacity_target_gw` + `target_year`; phase-out = linearly
interpolate today→zero by the retirement year. Requires an **installed-GW baseline per
country** added to the profile JSON (today stores shares/CF, not absolute GW). Delivers the
one-glance *"does my mix meet RE3020 / the coal phase-out target?"* output.

### Epic 3.3 — ITC / PTC / FIT / subsidy (shared `policy_support` block)  `P1` / `S`

One shared mechanism, structurally identical to `carbon_price`:

- **ITC** — multiplicative discount on `capex_usd_kw` before CRF (`effective_capex = capex × (1 − itc_rate)`).
- **PTC / FIT** — additive **negative** `$/MWh` term in the generator breakdown (own
  `policy_support` line so pre/post-subsidy cost are both visible).
- **FIT/CfD de-risking** — optional per-generator `discount_rate` override (lower WACC) into CRF.
- Inputs: `itc_rate` (0–1), `ptc_usd_mwh` (+ `eligibility_years`), eligible-generator set.
- Document simplifications: one-way subsidy proxy only; CfD two-sided clawback needs a market
  price series (out of scope); for single-year snapshot, assume all new-build is in the credit window.

### Epic 3.4 — Carbon-price extensions  `P2` / `S`

Extend the existing `carbon_price` (`schemas.py:43`): `carbon_price_escalation_pct_yr` (rising
path, pairs with Epic 2.5 time-horizon), `carbon_intensity_cap_tco2_mwh` (hard ceiling → penalty
on breach), `tax_credit_usd_mwh` (reverse subsidy for low-carbon).

### Epic 3.5 — Fuel import tariff  `P2` / `S`

Surcharge on `fuel_usd_mmbtu` (`fuel_tariff_pct` or `tariff_usd_mmbtu`) before the fuel-cost
multiplication in `_generator_breakdown`, gated by an `import_dependency_fraction` flag per
generator (most relevant to KR/JP, ~100% fossil import). **Build with** the energy-security KPI
(Epic 2.6) — shared field, more compelling together. (CBAM-style electricity border levy needs a
cross-border term — defer to sector-coupling / interconnection work, Epic 3.9.)

### Epic 3.6 — Grid integration cost re-enable  `P2` / `M`

Re-enable the disabled integration feedback (hardwired `0.0`, `lcoe_engine.py:162`). Either
(a) `integration_cost_policy_usd_mwh` fixed policy lever, or (b) dynamic
`integration_cost_func(vre_share, ...)` via the existing function catalog. Closes the
`integration_cost_func`-unused gap without reviving the full legacy parametric engine.

### Epic 3.7 — Demand-side management / efficiency  `P1`–`P2` / `M`

Two mechanisms:

- **Efficiency (level shift)**  `P1` / `S` — `efficiency_rate` preset scaling
  `annual_demand_twh × (1 − rate)`. Trivial, reuses existing plumbing. Ship early (rides on
  Phase 2 demand params).
- **Demand response (shape shift)**  `P2` / `M` — `dr_peak_shaving_fraction` sibling to the
  existing `_EV_SHORT_STORAGE_RELIEF_PER_UNIT` mechanism (`lcoe_engine.py:21`): peak-shave /
  valley-fill the `demand_norm` shape, reducing peak `demand_gw` and `storage_short_shift_gwh`.
  Validate against the existing EV-relief regression tests as a template.

### Epic 3.8 — Storage mandate & reserve margin  `P3` / `M`

`ess_minimum_capacity_gw` (policy floor) and `reserve_margin_pct` check on
`max(unserved_gw) / max(demand_gw)` in `dispatch_hourly`. The reserve-margin enforcement (grow
backup capacity on breach) needs constraint propagation into greedy merit-order — heavier; defer.

### Epic 3.9 — Multi-year retirement pathways & CBAM  `P3` / `L`  *(defer last)*

Coal/nuclear multi-year phase-out **pathways** (vs. single-year targets in 3.2) via the Epic 2.5
time-horizon loop. CfD clawback and CBAM cross-border electricity levy need new state (market
price series, interconnection term) — defer to the parameter / sector-coupling phases.

---

## Phase 4 — Sector coupling (demand-side blocks)

Add controllable / curtailment-seeking loads to the hourly demand profile **before** dispatch.
The pattern — *"add a stylized 8760h shape to `demand_norm`"* — is validated first by EV, then
reused. Sequenced per the research recommendation.

### Epic 4.1 — Power ↔ Transport / EV charging  `P1` / `M`  *(ship first)*

Upgrade EV from the crude storage-relief proxy (`_EV_SHORT_STORAGE_RELIEF_PER_UNIT`) to an
explicit load:

- Add a stylized EV charging kernel (bimodal unmanaged evening peak vs. flat / midday-shifted
  smart) to `demand_norm`, scaled by `ev_penetration × annual_demand_twh × kwh_per_ev_year`.
- `managed_charging_fraction` slider blending unmanaged (worsens peak / storage) ↔ smart
  (fills troughs, lowers intraday storage).
- Optional V2G: small bidirectional battery fed into existing `_size_storage_from_pattern`.
- Reuses ~100% of existing machinery (demand profile, dispatch, storage sizing). Highest value,
  smallest self-contained PR.

### Epic 4.2 — Power ↔ Heat / heat pumps  `P2` / `L`

- `heat_pump_electrification_share` + a **new** temperature-driven winter-peaked heat-demand
  shape (extend the `winter_component` term in `hourly_profiles.py:88`).
- Temperature-dependent COP function (default When2Heat-style `COP(T) = a − b·T`); hourly elec
  load `= heat_shape_th / COP`, added to `demand_norm`.
- Thermal-storage / DH flexibility as a tunable N-hour rolling-average smoothing window (4–24h) —
  avoids a full state-of-charge model.
- New physics (temperature-dependent efficiency, seasonal peakiness) — needs country
  heating-degree-day data not yet in profiles. Highest-value coupling after EV.

### Epic 4.3 — Power ↔ Hydrogen / electrolysis  `P2` / `L`

Electrolyzer as a **curtailment-seeking** load layered on the merit-order dispatch (not a new
solve): a post-dispatch pass over the surplus series the engine already computes
(`_size_storage_from_pattern`-style, `dispatch_engine.py:243`). `electrolyzer_capacity_gw`,
efficiency (kWh/kg H2), min-utilization floor → LCOH side-metric. Optional H2-to-power as a third
storage tier (low round-trip, high duration) sized off the same seasonal surplus pattern.

### Epic 4.4 — Industry electrification  `P3` / `L`

Mostly-flat / shift-scheduled new load block: `industry_electrification_twh` + a weekday/shift
shape (extend `business_hours` / `weekend` terms in `hourly_profiles.py:91-92`). Small
`flexible_load_fraction` with an intraday shift window. Data-hungry (sub-sector shapes,
fossil-displacement factors) and less genuinely flexible — after the simpler couplings validate
the pattern.

### Epic 4.5 — Power ↔ Gas / power-to-gas  `P3` / `L`  *(defer last)*

Reuse the existing seasonal (long-duration) storage tier in `_ess_metrics` with a power-to-gas
capex + much-lower round-trip-efficiency overlay; discharge routes through the existing
`gas_ccgt` slot via a `green_gas_blend_fraction` on its fuel cost + emission factor. Cheapest to
code, but low round-trip efficiency / high cost make it speculative — position as a
"long-horizon / high-VRE-share" toggle once the storage tab exists.

### Epic 4.6 — Multi-layer demand & storage diversity  `P3` / `L`

- **Unified demand layers** — refactor `dispatch_hourly` to accept a `demand_profile_layers`
  dict (`electricity_base`, `heating`, `ev_charging`, `hydrogen`, `industrial`) with firm /
  elastic / flexible tiers and price/carbon signals. The convergent endpoint of Epics 4.1–4.4.
- **Storage diversity** — generalize `_ess_metrics` from two fixed tiers (4h / 168h) to an
  `ess_techs` list (battery / H2 / pumped-hydro / thermal), each with duration, capex, RT
  efficiency, location constraint; size a cost-optimal stack over the surplus/deficit pattern.

---

## Cross-cutting (all phases)

- **`P2` / `M`** — **Uncertainty quantification** beyond weather jitter: user-configurable
  ±CAPEX / ±fuel / ±demand bands (Monte Carlo / sensitivity sampling) driving the
  p10/median/p90 display (roadmap A3). Extends the existing ensemble.
- **`P3` / `M`** — **Public API + embeddable widget** for the research community (roadmap I3).
- **`P3` / `L`** — **WCAG 2.1 AA + i18n** (ko-KR, ja-JP) — procurement requirement in many
  jurisdictions (roadmap I4).
- **`P3` / `S`** — **Local-currency display** (roadmap D2) and **historical calibration view**
  against actuals (roadmap D3).
- **Every math change** — `ALGORITHM.md` equation (LaTeX + ASCII) + `np.testing.assert_allclose`
  regression test with explicit `rtol` / `atol` (`CLAUDE.md`).

---

## Known technical debt (still valid)

| # | Issue | Location | Risk | Addressed by |
|---|-------|----------|------|--------------|
| 2 | `customProfile` not passed to `calculateSystem` in sensitivity charts | `frontend/components/ProfileAnalysis.tsx` | Parameter edits invisible in carbon-price / ESS-cost curves | Epic 0.1 |
| 3 | `custom_params` not passed to `generateGrid` in mix explorer | `frontend/components/GeneratorMixPlotter.tsx` | User cost edits ignored in the 3D sweep | Epic 0.1 |
| 4 | `backup_flexibility` computed & returned but never displayed | `backend/core/lcoe_engine.py:168` | Users can't see why nuclear-heavy mixes curtail VRE | Epic 0.2 |
| 5 | Long-duration ESS LCOE aggregated into one bar, not broken out | cost-stack chart / `CostBreakdownChart` | Obscures the cost penalty of 80%+ VRE pathways | Epic 0.2 |
| 6 | No validation that `shares` / `capacities_gw` keys match the profile generator list | `backend/models/schemas.py` | Custom-profile gen add/remove misattributes silently | Epic 0.1 |
| — | No backend automated test suite | `backend/tests/` | Zero regression coverage; high risk for policy / publication use | Epic 0.3 |
| — | Grid integration cost hardwired to `0.0`; feedback loop disabled | `lcoe_engine.py:162,328,345` | VRE integration cost invisible; `integration_cost_func` unused | Epic 3.6 |

## Open questions / decisions needed

1. **Legacy function catalog** — delete `cf_eff_func` / `curtailment_func` /
   `integration_cost_func` from profile JSON, or keep quarantined behind a "legacy" flag?
   (`integration_cost_func` is the one candidate for revival via Epic 3.6.)
2. **Installed-GW baseline** — country profiles store shares/CF, not absolute installed GW.
   Capacity targets, phase-out pathways, and V2G fleet sizing all need it. Source now (blocks
   Epics 3.2, 3.9, 4.1-V2G) or defer?
3. **RPS eligibility definition** — does nuclear/hydro count toward the renewable target? Needs
   a per-jurisdiction config toggle (varies KR vs AU vs JP vs future US/DE).
4. **Time-horizon vs. single-year** — how much of Phase 3 should assume the Epic 2.5 multi-year
   loop exists? Escalating carbon price, retirement pathways, and cost-forecast controls all
   depend on it. Prioritize the time-horizon loop earlier?
5. **Merit-order semantics** — merit order is user-set, not cost-optimized, so carbon price
   changes cost/emissions but not the dispatch stack. Keep as a documented limitation, or add an
   optional cost-minimizing dispatch mode (a real architectural change)?
6. **CAPEX learning curves** — whose trajectories (IEA STEPS/APS/NZE, NREL ATB, Lazard)? Needed
   for Epics 2.5 and cost-forecast controls; a licensing / provenance decision.
