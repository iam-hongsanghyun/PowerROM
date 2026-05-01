# ROM-Power — Policy Development Roadmap

*Prioritised from the perspective of policy makers and their actual decision-making needs.*  
*Last updated: 2026-05-01*

---

## How to read this list

Each item is tagged with:
- **Impact** — how directly it affects the policy value of the tool
- **Effort** — rough implementation cost (S / M / L)
- **Audience** — who benefits most (Analyst / Decision-maker / Public)

Items are ordered within each tier by impact-to-effort ratio.

---

## Tier 1 — Critical: without these, the tool cannot be used in a policy brief

### 1.1 Scenario save and compare
**Impact: Very High | Effort: M | Audience: Analyst, Decision-maker**

Policy analysis is fundamentally comparative. A minister or committee needs to see "Scenario A vs B vs C" on one chart, not flip between tabs. Currently there is no way to lock a scenario and run another.

- Named scenario slots ("Current policy", "2030 target", "Accelerated wind")
- Side-by-side LCOE, emissions, ESS cost table
- Overlay curves on the same chart with distinct colours
- Export the full comparison as a single PDF or Excel sheet

---

### 1.2 Export to PDF / PowerPoint-ready image
**Impact: Very High | Effort: M | Audience: Decision-maker, Public**

Every output chart lives behind a browser tab. Policy makers need a clean, branded PDF they can attach to a cabinet memo or drop into a slide deck. Nothing leaves the tool in a presentable format today.

- One-click PDF of the current scenario (charts + key numbers in a fixed layout)
- High-resolution PNG / SVG export per chart
- Auto-generated summary box: LCOE, emissions, ESS need, curtailment, annual cost
- Optional letterhead / institution name field

---

### 1.3 Clear data provenance and vintage on every number
**Impact: Very High | Effort: S | Audience: Analyst, Decision-maker**

Policy documents are scrutinised. If a number cannot be traced to a source and a year, it will not survive peer review or parliamentary questions. Currently sources exist in the JSON but are not surfaced in the UI at all.

- "Sources" tooltip / panel on every chart and every parameter row
- Data vintage label ("IEA WEO 2024") visible without opening Parameters
- Flag when the user's custom profile differs from the default, with a note that it has not been peer-reviewed
- "Methodology" one-pager accessible from the status bar

---

### 1.4 Uncertainty / sensitivity bands on all charts
**Impact: High | Effort: M | Audience: Analyst**

A single-point LCOE estimate is not credible in a policy context. Decision-makers need to know how sensitive the result is to key assumptions (CAPEX ±20%, carbon price ±30%, fuel price shocks).

- Shaded confidence band on the system LCOE curve driven by ±% sliders
- Tornado chart: which parameter moves the answer the most?
- Monte Carlo option (100-run quick sweep) showing the result distribution
- Highlight the "robust" VRE share — the share where all plausible futures are competitive

---

### 1.5 Carbon budget alignment indicator
**Impact: High | Effort: S | Audience: Decision-maker, Public**

The most common policy question is "does this mix get us to net-zero by 2050?" The tool currently shows emission intensity but does not relate it to any target.

- User-configurable emission target line on the emission intensity chart (e.g. "Net-zero = 0 gCO₂/kWh by 2050")
- Annual emissions (Mt CO₂) shown alongside intensity
- Simple traffic-light badge: Red / Amber / Green relative to a chosen national target
- Cumulative carbon budget tracker if a pathway (year-by-year) is defined

---

## Tier 2 — High Priority: needed for meaningful policy analysis

### 2.1 Multi-country / region comparison
**Impact: High | Effort: M | Audience: Analyst, Decision-maker**

"How does Korea's planned mix compare to Australia or Japan?" is a natural question in any multilateral climate dialogue. The tool has three country profiles but no way to put them side by side.

- Select up to 3 countries and compare their LCOE curves on one chart
- Normalised comparison view (index to current mix = 100)
- Table: LCOE, emissions, ESS, curtailment for the same VRE share across countries
- Add new country profiles without code changes (upload a JSON)

---

### 2.2 Time-horizon pathway (2030 / 2040 / 2050)
**Impact: High | Effort: L | Audience: Decision-maker, Public**

Policy targets are defined in years, not VRE shares. A 2030 target of "40% renewables" needs to be translated into cost and emissions on a timeline, not just a point on a static curve.

- Year slider that applies technology cost trajectories (CAPEX learning curves per generator)
- Emit a table: for each target year, minimum cost share, emissions, ESS requirement
- Highlight the year when the system becomes cost-competitive without a carbon price
- Paris-aligned pathway pre-loaded as a reference scenario

---

### 2.3 Energy security metrics
**Impact: High | Effort: M | Audience: Decision-maker**

LCOE alone does not capture what keeps politicians awake at night. Fuel import dependency, generation adequacy during a cold dark week, and exposure to gas price spikes are first-order policy concerns.

- Fuel import cost (USD/yr) as a function of the generation mix
- Import dependency index: (fuel imports) / (total system energy)
- Adequacy indicator: does the mix have enough dispatchable capacity to cover peak demand?
- Supply security score: weighted index combining import dependency, VRE curtailment, backup margin

---

### 2.4 Additional country profiles (DE, GB, FR, CN, IN, US)
**Impact: High | Effort: M per country | Audience: All**

The tool covers only KR, AU, JP. Global climate policy requires comparison with major emitters and clean-energy leaders.

Priority order (by policy relevance):
1. Germany (DE) — Energiewende benchmark, European context
2. United Kingdom (GB) — offshore wind leader, carbon pricing reference
3. India (IN) — largest developing-country transition story
4. United States (US) — IRA policy context, regional grid diversity
5. China (CN) — largest absolute emissions, fastest VRE scale-up
6. France (FR) — nuclear-dominated system for contrast

---

### 2.5 Cost in local currency
**Impact: Medium-High | Effort: S | Audience: Decision-maker, Public**

Policy documents are written in local currency. Presenting USD figures to a Korean ministry or an Australian state government creates unnecessary friction.

- Currency selector (USD, KRW, AUD, JPY) with a user-editable exchange rate
- All cost outputs rendered in the selected currency
- Rate source and date shown ("FX: 1 USD = 1,380 KRW, BOK 2025-05-01")

---

### 2.6 Demand-side integration (EV, heat pumps, demand response)
**Impact: Medium-High | Effort: M | Audience: Analyst**

EV penetration already exists as a slider but only offsets ESS. The tool should recognise that demand flexibility is a substitute for both ESS and dispatchable capacity.

- Demand response capacity (GW) as a slider; reduces effective ESS requirement
- Heat pump electrification shifts load profile → affects VRE curtailment timing
- Industrial electrification adds peak demand → raises adequacy requirements
- Sensitivity: "How much cheaper does storage need to be to make 80% VRE viable without demand response?"

---

### 2.7 Capacity mix output (GW, not just share)
**Impact: Medium-High | Effort: S | Audience: All**

Policy targets and infrastructure plans are stated in GW, not percentages. "Build 50 GW of solar" is the real policy instrument; the tool only shows shares.

- GW installed capacity for each generator, derived from annual demand + CF
- Peak capacity check against peak demand (user-editable GW)
- Land area estimate (km² per technology) based on installed GW
- Annual investment cost (USD/yr) from the CAPEX and lifetime assumptions

---

## Tier 3 — Important: significantly increases usability and credibility

### 3.1 Guided onboarding for non-technical users
**Impact: Medium | Effort: M | Audience: Decision-maker, Public**

The current interface assumes familiarity with LCOE, VRE share, and ESS. A minister's policy officer may not have this background.

- 3-step onboarding wizard: "Pick a country → adjust your energy mix → see the cost"
- Tooltip glossary on every technical term (LCOE, CRF, curtailment, etc.)
- "What does this mean?" explainer cards on the charts
- Pre-loaded policy scenario buttons: "IEA Net Zero 2050", "Current national plan", "Coal phase-out by 2035"

---

### 3.2 Historical calibration view
**Impact: Medium | Effort: M | Audience: Analyst**

"How well does the model reproduce what actually happened?" is the first question any serious analyst will ask before trusting a projection.

- Upload historical data (year, VRE share, system LCOE, emission intensity)
- Overlay model curve on historical actuals
- Compute and display model error (RMSE, MAPE)
- Calibration mode: auto-fit profile parameters to historical data

---

### 3.3 Just transition indicators
**Impact: Medium | Effort: M | Audience: Decision-maker, Public**

Coal phase-out decisions affect workers and communities. No transition plan is credible without addressing this.

- Estimated jobs displaced (coal / gas workers) as a function of share reduction
- Estimated jobs created (construction + O&M for VRE + ESS)
- Net employment change by 2030 / 2040
- Affected communities map (optional, country-specific data)

---

### 3.4 API / embeddable widget
**Impact: Medium | Effort: M | Audience: Analyst**

Research institutions and think tanks want to embed the calculator in their own reports or websites, or call it from Python/R for batch analysis.

- Public read-only REST API with rate limiting
- Embeddable iframe widget (single chart, configurable)
- Python client library (`pip install powerrom`)
- OpenAPI documentation at `/docs`

---

### 3.5 Shareable URL with encoded scenario
**Impact: Medium | Effort: S | Audience: All**

"Send me the scenario you were looking at" is a constant workflow. Currently there is no way to share a specific set of inputs.

- Encode all slider values into the URL hash on every change
- Paste the URL to restore the exact scenario in a new browser
- Short-link generation (e.g. `power-rom.vercel.app/s/abc123`)
- Share button that copies the URL to clipboard

---

### 3.6 Model validation and limitation disclosure
**Impact: Medium | Effort: S | Audience: Analyst, Decision-maker**

A reduced-order model has known limitations. Hiding them erodes trust; disclosing them builds it.

- Prominent "Model Limitations" section accessible from the home page
- Accuracy statement: "This model reproduces Korean historical LCOE within ±8% for 2019–2023"
- List of what the model does NOT capture (hourly dispatch, grid topology, ancillary services)
- Citation: how to reference ROM-Power in a publication

---

### 3.7 Mobile / tablet responsive layout
**Impact: Medium | Effort: M | Audience: Decision-maker, Public**

A minister reviewing a briefing on a tablet at a summit cannot use the current layout. The control panel and charts do not reflow for small screens.

- Collapsible control panel on mobile
- Charts scale to single-column layout below 768px
- Touch-friendly sliders
- Key numbers (LCOE, emissions, ESS) in a sticky top bar on mobile

---

## Tier 4 — Quality & Maintenance

### 4.1 Backend test suite
**Impact: Low-Medium | Effort: M | Audience: Developers**

The calculation engine has no automated tests. A parameter change or profile update could silently break the numbers.

- Unit tests for `lcoe_engine.py`: regression values for KR, AU, JP with default profiles
- Property-based tests: LCOE must increase monotonically with carbon price
- CI check on every pull request
- Alert when a profile JSON change shifts any key output by more than 5%

---

### 4.2 Profile versioning and changelog
**Impact: Low-Medium | Effort: S | Audience: Analyst**

Country profiles will be updated as new IEA/IRENA data is published. Users need to know which version they are working with.

- Semantic version number on each country profile JSON (`"version": "2024.1"`)
- Changelog entry per version (what changed, why, source)
- UI shows profile version in the Parameters toolbar
- Notify users when a newer profile version is available

---

### 4.3 Accessibility (WCAG 2.1 AA)
**Impact: Low-Medium | Effort: M | Audience: Public**

Government tools are often required to meet accessibility standards for public-sector procurement.

- All chart colours pass contrast ratio requirements
- Keyboard navigation through all controls
- Screen-reader labels on all interactive elements
- Data table alternative for every chart

---

### 4.4 Internationalisation (i18n)
**Impact: Low | Effort: L | Audience: Public**

The tool currently uses English only. Korean-language government users would benefit from a Korean UI.

- Korean (ko-KR) translation for all UI labels, tooltips, and chart axes
- Japanese (ja-JP) translation
- Number formatting per locale (Korean: 억/조, Japanese: 万/億)
- Right-to-left layout support for future Arabic translations

---

## Known technical debt (fix before scaling)

| Item | File | Risk if not fixed |
|------|------|-------------------|
| `ev_offset_gwh_per_unit = 18000` in KR.json — this implies 18 TWh per EV unit; units need verification | `backend/data/country_profiles/KR.json` | ESS sizing is wrong whenever EV penetration > 0 |
| `ProfileAnalysis.tsx` does not use `customProfile` — profile edits do not update the sensitivity charts | `frontend/components/ProfileAnalysis.tsx` | Scenario analysis ignores user's parameter changes |
| `GeneratorMixPlotter.tsx` does not pass `customProfile` to `calculateBatch` | `frontend/components/GeneratorMixPlotter.tsx` | Mix explorer ignores user's parameter changes |
| `backup_flexibility` field computed in backend but not displayed anywhere in the UI | `backend/core/lcoe_engine.py` | Curtailment mechanism is invisible to the user |
| Long-duration ESS LCOE component not broken out in the cost breakdown stack chart | `frontend/components/charts/CostBreakdownChart.tsx` | Users can't see the cost penalty of high-VRE pathways |
| No validation that `shares` keys in a request match the profile's generator list | `backend/api/calculate.py` | Silent misattribution when custom profiles add/remove generators |

---

*This document is maintained by PLANiT Institute. Submit additions via GitHub Issues.*
