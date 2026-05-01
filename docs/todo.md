# ROM-Power — Development Roadmap

*Organised by the two primary user perspectives the tool must serve.*  
*Last updated: 2026-05-01 · PLANiT Institute*

---

## Product vision: two-layer architecture

The tool currently models **Layer 1** (policy layer). Commercial viability requires **Layer 2** (business impact layer) that translates policy outcomes into corporate and household consequences.

```
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 1 — Policy Layer (current)                               │
│                                                                 │
│  Current government policy → scenario space → uncertainty band  │
│  VRE mix · carbon price · system LCOE · emissions · ESS need    │
└────────────────────────────┬────────────────────────────────────┘
                             │  system LCOE  ×  retail markup
                             ↓
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 2 — Business Impact Layer (to build)                     │
│                                                                 │
│  Electricity price range · Fuel cost exposure · Carbon cost     │
│  Investment opportunity · Competitiveness vs. peer countries    │
└─────────────────────────────────────────────────────────────────┘
```

**Tags used below:**  
Priority: `P1` must-have · `P2` high value · `P3` nice-to-have  
Effort: `S` < 1 week · `M` 1–3 weeks · `L` > 1 month

---

---

# Perspective 1 — Policy Decision-Maker (정책결정자)

*Primary question: "Where is our current policy on this landscape, how uncertain is it, and can I defend these numbers in a committee room?"*

---

## A. Anchor current policy and show deviation range

### A1 · Mark the current government policy scenario `P1 · S`
The most natural starting question from any minister: *"Show me where we are now."* Every chart should have a clear reference marker for the current national energy plan.

- Dedicated "Current Policy" scenario that loads the government's published generation mix and carbon price target
- Vertical marker on the LCOE and emissions curves showing the policy's position
- Label shows: policy name, reference year, source document
- Separate from the user's exploration so it cannot be accidentally overwritten

### A2 · Scenario save, label, and side-by-side compare `P1 · M`
Policy analysis is inherently comparative. A decision-maker needs to see "Current Policy vs. Accelerated VRE vs. Nuclear Extension" on a single chart with a summary table.

- Named scenario slots (up to 5) with user-defined labels
- Overlay all saved scenarios on every chart with distinct colours
- Summary comparison table: LCOE · emissions · ESS · curtailment · annual system cost
- Highlight which scenario is cheapest, which meets the carbon target

### A3 · Policy deviation uncertainty bands `P1 · M`
No energy plan is executed exactly as written. Decision-makers need the plausible range around the policy scenario, not just a central estimate.

- ± slider for key uncertainties: CAPEX (±20%), fuel price (±30%), demand growth (±10%)
- Shaded band on every chart showing the resulting output range
- "Robust zone" highlight: the VRE share range where all uncertainty bounds still beat the status quo on cost or emissions
- Tornado chart: which assumption moves the answer the most?

### A4 · Carbon budget alignment indicator `P1 · S`
The question "does this scenario get us to net-zero by 2050?" must be answerable in one glance.

- User-configurable national emission target line on the emission intensity chart
- Traffic-light badge: Red / Amber / Green vs. the selected target
- Annual Mt CO₂ displayed alongside intensity (gCO₂/kWh)
- Cumulative carbon budget tracker when a year-by-year pathway is defined

---

## B. Enable credible, reportable outputs

### B1 · Export to PDF / PowerPoint-ready image `P1 · M`
Results that cannot leave the browser cannot appear in a cabinet memo.

- One-click PDF of the current scenario: charts + key numbers in a clean fixed layout
- High-resolution PNG / SVG export per chart
- Auto-generated executive summary box: LCOE · emissions · ESS · curtailment · annual cost
- Optional institution name / logo field in the report header

### B2 · Shareable URL encoding the full scenario state `P1 · S`
"Send me the scenario you were looking at" is the most common inter-office request.

- All slider values encoded into the URL on every change
- Paste the URL to restore the exact scenario in any browser
- Short-link generation (`power-rom.vercel.app/s/abc123`)
- Copy-to-clipboard share button

### B3 · Data provenance visible on every number `P1 · S`
Any unattributed assumption will be challenged in parliamentary questioning.

- Source tooltip on every parameter row and chart: "IEA WEO 2024", "IRENA 2024"
- Data vintage label always visible (not hidden in Parameters)
- Prominent flag when the user's custom parameters differ from the peer-reviewed defaults
- "Methodology" summary page reachable from the status bar, with citation format for publications

### B4 · Model limitations disclosure `P2 · S`
Disclosing what the model does *not* capture builds more trust than hiding it.

- Prominent "Model scope and limitations" panel on the home page
- Accuracy statement: "Reproduces Korean historical LCOE within ±N% for 2019–2023"
- Explicit list of what is excluded: hourly dispatch, grid topology, ancillary services, network costs
- Link to full methodology document

---

## C. Enable comparative and forward-looking analysis

### C1 · Multi-country comparison `P2 · M`
"How does Korea's planned mix compare to Japan or Germany?" is the first question in any multilateral climate dialogue.

- Select up to 3 countries and overlay their LCOE curves on one chart
- Same-VRE-share comparison table across countries
- Export the comparison as a single table or chart
- Ability to upload a new country profile (JSON) without code changes

### C2 · Time-horizon pathway (2030 / 2040 / 2050) `P2 · L`
Policy targets are defined in years, not VRE shares.

- Year slider that applies technology cost learning curves (CAPEX trajectories per generator)
- For each target year: minimum-cost share, emissions, ESS requirement, total system cost
- Highlight the year when the system becomes cost-competitive without a carbon price
- Paris-aligned reference pathway pre-loaded as a comparison baseline

### C3 · Energy security metrics `P2 · M`
LCOE alone does not reflect what keeps energy ministers awake at night.

- Fuel import cost (USD/yr and % of system cost) as a function of the generation mix
- Import dependency index: imported fuel energy / total system energy
- Adequacy indicator: does the dispatchable capacity cover peak demand?
- Supply security composite score (import dependency + curtailment risk + backup margin)

### C4 · Capacity mix output (GW, not just share) `P2 · S`
Policy targets and infrastructure plans are stated in gigawatts. The tool only shows shares.

- GW installed capacity per generator derived from annual demand + capacity factor
- Peak capacity check against user-editable peak demand (GW)
- Land area estimate (km²) based on installed GW per technology
- Annual investment flow (USD/yr) implied by the planned build-out

### C5 · Additional country profiles `P2 · M per country`
Three countries (KR, AU, JP) is not enough for international policy benchmarking.

Priority order by policy relevance:
1. Germany (DE) — Energiewende benchmark, European carbon market
2. United Kingdom (GB) — offshore wind leader, carbon pricing reference
3. India (IN) — largest developing-country transition
4. United States (US) — IRA policy context, regional grid diversity
5. China (CN) — largest absolute emissions, fastest VRE scale-up
6. France (FR) — nuclear-dominated system as contrast case

---

## D. Accessibility and trust for diverse policy audiences

### D1 · Guided onboarding for non-technical staff `P2 · M`
A minister's policy officer may not know what LCOE or VRE share means.

- 3-step wizard: "Pick a country → adjust your energy mix → see the cost and emissions"
- Tooltip glossary on every technical term
- "What does this mean for policy?" explainer card on each chart
- Pre-loaded scenario buttons: "IEA Net Zero 2050", "Current national plan", "Coal phase-out by 2035"

### D2 · Local currency display `P2 · S`
Policy documents are written in local currency. USD figures create friction with Korean or Australian officials.

- Currency selector (USD, KRW, AUD, JPY) with user-editable exchange rate
- All cost outputs in selected currency
- Rate source and date shown ("FX: 1 USD = 1,380 KRW · BOK 2025-05-01")

### D3 · Historical calibration view `P3 · M`
"How well does this model reproduce what actually happened?" is the first question from any serious analyst.

- Upload historical data (year, VRE share, system LCOE, emission intensity)
- Overlay model curve on historical actuals
- Display model error (RMSE, MAPE)
- Optional: auto-fit profile parameters to historical data

---

---

# Perspective 2 — Business User (비즈니스 사용자)

*Primary question: "Given the policy scenario and its uncertainty, what happens to my electricity bill, my fuel costs, and where are the investment opportunities?"*

*All of Section 2 requires the new **Business Impact Layer** described in the architecture above.*

---

## A. Translate policy scenarios into electricity prices

### A1 · System LCOE → retail electricity price converter `P1 · S`
System LCOE is a wholesale planning metric. Businesses pay retail prices that include network charges, taxes, and retailer margins. This is the single most important translation step.

- Country-specific retail markup coefficient: `retail_price = system_LCOE × markup_factor`
- Markup factor is editable (default sourced from national regulator data)
- Components shown separately: generation cost · network · taxes / levies · retail margin
- Result: retail price in $/MWh and local currency / kWh

### A2 · Company electricity cost calculator `P1 · S`
Once retail price is known, the business impact is straightforward: consumption × price.

- User inputs: annual electricity consumption (MWh/yr), peak demand (kW)
- Output: annual electricity bill under baseline, low, and high policy scenarios
- Year-by-year bill projection from 2025 to 2050 (uses time-pathway from Policy Layer)
- Delta from current: how much more or less per year vs. today?

### A3 · Electricity price uncertainty range `P1 · S`
The most valuable output for a CFO is not a point estimate but a defensible range for budget planning.

- Derives directly from the policy uncertainty bands (Policy Layer A3)
- Output: electricity price in $/MWh — pessimistic / central / optimistic
- "Planning range" that a CFO can use for scenario budgeting
- Probability framing: "70% confidence the price will be between X and Y by 2030"

---

## B. Quantify risk exposure

### B1 · Carbon cost exposure `P1 · M`
Carbon pricing affects businesses both directly (own emissions) and indirectly (embedded in electricity).

- User inputs: direct emission sources (tonnes CO₂/yr by fuel type), industry sector
- Calculates: direct carbon cost at current and projected carbon prices
- Calculates: embedded carbon cost in electricity consumption
- Total carbon cost exposure under low / central / high carbon price scenarios
- Sensitivity: "A $10/tonne carbon price increase costs this company $X/yr"

### B2 · Fuel cost exposure `P1 · M`
Many industrial users have direct fuel inputs (gas for process heat, coal for industrial heat) that are separately affected from electricity.

- User inputs: annual fuel consumption by type (natural gas MMBtu, coal tonnes, oil barrels)
- Links fuel prices to the policy scenario's assumptions (same data source)
- Output: annual fuel cost under baseline vs. accelerated-VRE scenarios
- Note: high VRE reduces gas demand system-wide → likely lower gas prices → fuel cost benefit

### B3 · Business risk dashboard `P2 · M`
Summarises all energy-related risk exposures in one view for C-suite use.

- Total energy cost (electricity + direct fuel + carbon) by scenario
- Energy cost as % of revenue / COGS (user inputs their revenue figure)
- Year-on-year change trajectory to 2030 / 2040
- Key risk drivers ranked: which factor (electricity, gas, carbon) drives the most uncertainty?
- One-page PDF export for board reporting

---

## C. Identify investment opportunities

### C1 · Grid parity calculator `P1 · S`
"When does it become cheaper for me to generate my own power than to buy from the grid?"

- Compares projected retail electricity price (from A1) vs. on-site solar LCOE
- On-site solar LCOE uses profile capex/cf data for the user's country + roof / ground-mount factor
- Outputs: current gap ($/MWh), year of grid parity, payback period
- Sensitivity to discount rate, system size, self-consumption ratio

### C2 · On-site solar + storage investment sizing `P2 · M`
Once grid parity is established, the user needs to know how much to build and what it costs.

- User inputs: available roof / land area (m²) or target self-sufficiency (%)
- Outputs: system size (kW), annual generation (MWh), upfront cost, annual savings, IRR
- With storage: battery size recommendation to maximise self-consumption
- Under different policy scenarios: IRR range (best case / worst case)

### C3 · Market investment opportunity sizing `P2 · M`
For energy companies, developers, and financial investors: where is the capital going in this transition?

- By scenario: annual investment needed in solar, wind, storage, gas backup (USD/yr)
- Cumulative investment to 2030 / 2040 / 2050 per technology
- Implied project pipeline: number of projects, average project size
- Revenue opportunity: merchant LCOE vs. contracted price gap

---

## D. Benchmark competitiveness

### D1 · International electricity price comparison `P2 · S`
"Are my competitors in Germany or Japan paying less for electricity under their energy mix?"

- Same-year retail price comparison across all available countries
- Under the same carbon price assumption to isolate mix effect from policy effect
- Export-oriented industry premium: domestic price − international benchmark
- "Competitiveness risk": if domestic price rises above X, the industry faces import competition

### D2 · Sector competitiveness impact `P2 · M`
Energy-intensive industries (steel, chemicals, cement, aluminium) face structural competitiveness risk if domestic electricity costs diverge from global peers.

- User selects sector; model loads typical energy intensity (MWh per tonne of output)
- Output: energy cost per unit of output by scenario and year
- Comparison vs. same sector in peer countries
- Carbon border adjustment (CBAM) cost if exporting to carbon-priced markets

### D3 · Household electricity bill impact `P3 · S`
The most politically visible metric — and the simplest to compute.

- Average household consumption (kWh/yr) per country, editable
- Monthly bill under each scenario in local currency
- Delta from today framed as: "equivalent to X cups of coffee per month"
- Makes the tool useful for public communication and journalism

---

---

# Shared infrastructure (serves both perspectives)

### I1 · Profile versioning and changelog `P2 · S`
Country profiles will be updated as IEA/IRENA publish new data. Users need to know which version they are using.

- Semantic version on each JSON profile (`"version": "2024.1"`)
- Changelog entry per version: what changed, why, source
- UI shows profile version in the Parameters toolbar
- Notification when a newer profile version is available

### I2 · Backend automated test suite `P2 · M`
The calculation engine has no automated tests. A parameter change could silently break the numbers.

- Regression tests for KR, AU, JP with default profiles (expected LCOE / emissions / ESS values)
- Property tests: LCOE must increase with carbon price; ESS must increase with VRE share
- CI check on every pull request
- Alert when any profile update shifts a key output by more than 5%

### I3 · Public API and embeddable widget `P3 · M`
Research institutions want to call the model from Python/R or embed it in their own reports.

- Read-only REST API with rate limiting
- Embeddable iframe for a single chart
- Python client library (`pip install powerrom`)
- OpenAPI documentation at `/api/docs`

### I4 · Accessibility and internationalisation `P3 · L`
Required for government procurement in most jurisdictions.

- WCAG 2.1 AA compliance: contrast ratios, keyboard navigation, screen-reader labels
- Korean (ko-KR) and Japanese (ja-JP) UI translations
- Number formatting per locale (Korean: 억/조, Japanese: 万/億)

---

---

# Known technical debt — fix before scaling

These are specific bugs in the current codebase that will cause silent errors as usage grows.

| # | Issue | File | Risk |
|---|-------|------|------|
| 1 | `ev_offset_gwh_per_unit = 18,000` — implies 18 TWh offset per EV fleet "unit"; physical units need verification against the EV penetration fraction definition | `KR.json`, `AU.json`, `JP.json` | ESS sizing is wrong whenever EV penetration > 0 |
| 2 | `ProfileAnalysis.tsx` does not pass `customProfile` to its `calculateSystem` calls — parameter edits (Apply button) do not update the sensitivity charts | `ProfileAnalysis.tsx` | Profile edits are invisible in the Profile tab |
| 3 | `GeneratorMixPlotter.tsx` does not pass `customProfile` to `calculateBatch` — mix explorer ignores user's parameter changes | `GeneratorMixPlotter.tsx` | Mix explorer ignores profile edits |
| 4 | `backup_flexibility` is computed in the backend and returned in the response but never displayed in the UI — users cannot see why curtailment is high in nuclear-heavy portfolios | `lcoe_engine.py`, `Dashboard.tsx` | Key model mechanism is invisible |
| 5 | Long-duration ESS LCOE is not broken out in the cost breakdown stack chart — only shown in the status bar | `CostBreakdownChart.tsx` | Users can't see the cost penalty of high-VRE pathways |
| 6 | No validation that `shares` keys in a request match the generator list in the profile — extra or missing keys fail silently | `backend/api/calculate.py` | Misattribution when custom profiles add or remove generators |
| 7 | `DEFAULT_SHARES` in Dashboard sums to 1.0 but does not correspond to any real country's current mix — should either be the country's actual current mix or documented as illustrative | `constants.ts`, `Dashboard.tsx` | Users may interpret the starting point as a national baseline |

---

*Maintained by PLANiT Institute. Propose additions via GitHub Issues.*
