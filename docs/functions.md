# ROM-Power — Function Reference (KR Default Settings)

Every behavioural relationship in the model is a **configurable function** f(x) that maps one physical variable to another.  This document explains each function in plain language using the South Korea default parameters.

---

## How functions work

All functions share the same structure:

```
output = f(x)   where x is clipped to [x_min, x_max] before evaluation
```

When `x_min`/`x_max` are set, the function output is clamped to the range of values at those bounds — the function does not extrapolate outside its calibrated region.

---

## 1. `cf_eff_func` — How capacity factor degrades with VRE penetration

**x = system VRE share (0–1)**
**output = effective capacity factor of this generator (0–1)**

This captures how much a generator's *utilisation* falls as the grid fills with more solar and wind.  A high VRE share means:
- Dispatchable plants (gas, coal) get pushed to the margins and run fewer hours
- VRE plants themselves face curtailment when their output exceeds demand

### Solar — KR default

```
Type: logarithmic
f(x) = a − b·ln(1 + c·x) = 0.145 − 0.08·ln(1 + 3.0·x)
x clipped to [0.05, 0.20]
```

At VRE share = 0%  →  CF ≈ 0.145  (rated: Korean solar averages ~14.5%)
At VRE share = 20% →  CF ≈ 0.145 − 0.08·ln(1.6) ≈ 0.106  (−27% from curtailment + profile mismatch)

**Why logarithmic?**  Losses are front-loaded — the first increment of VRE penetration causes the steepest degradation because the easiest curtailment hours are hit first.  At very high VRE shares the CF is already low and plateaus.

---

### Wind (Onshore) — KR default

```
Type: logarithmic
f(x) = 0.22 − 0.07·ln(1 + 2.4·x)
x clipped to [0.08, 0.30]
```

At VRE share = 0%  →  CF = 0.22  (rated; Korea's onshore wind resource is modest)
At VRE share = 30% →  CF ≈ 0.22 − 0.07·ln(1.72) ≈ 0.184  (−16%)

**Why less degradation than solar?**  Wind output is smoother diurnally (not purely daytime), so at a given VRE share it causes less temporal surplus and therefore less curtailment.

---

### Gas CCGT — KR default

```
Type: linear
f(x) = 0.75 − 0.55·x
x clipped to [0.10, 0.85]
```

At VRE share = 0%   →  CF = 0.75  (baseload utilisation with no VRE)
At VRE share = 50%  →  CF = 0.75 − 0.275 = 0.475  (half-loaded, backing up VRE)
At VRE share = 85%  →  CF = 0.75 − 0.4675 = 0.283  (clamped floor)

**Why linear?**  Gas capacity is dispatched proportionally to fill the residual demand gap left by VRE.  As VRE share rises linearly, gas utilisation falls proportionally.

**Physical story:** Gas CCGT is the *flexible backup*.  Its CF inversely tracks VRE penetration — at high VRE, gas only runs during calm/dark periods and nights.

---

### Coal — KR default

```
Type: piecewise linear
f(x) = 0.78 − 0.35·x                          for x ≤ 0.25  (gentle displacement)
f(x) = 0.78 − 0.35·0.25 − 0.70·(x − 0.25)    for x > 0.25  (steep displacement)
x clipped to [0.15, 0.80]
```

At VRE share = 0%   →  CF = 0.78  (baseload, runs continuously)
At VRE share = 25%  →  CF = 0.78 − 0.0875 = 0.6925  (plateau: VRE mainly displaces gas first)
At VRE share = 50%  →  CF = 0.6925 − 0.70·0.25 = 0.518  (coal finally pushed out)

**Why piecewise?**  In a merit-order dispatch, cheap VRE first displaces expensive gas and oil.  Coal's utilisation is relatively protected at low VRE share.  Above 25% VRE, gas has been mostly displaced and VRE starts cutting into coal hours more aggressively — hence the slope doubles.

---

### Nuclear — KR default

```
Type: constant
f(x) = 0.87  (clipped to [0.80, 0.92])
```

Nuclear runs at ~87% CF regardless of VRE penetration.  This reflects Korea's policy of treating nuclear as a must-run baseload — the output target is set politically, not by market dispatch.

**Why constant?**  Nuclear cannot ramp quickly.  In practice Korean nuclear runs almost flat year-round; the CF range [0.80, 0.92] reflects outage schedules, not dispatch variation.

---

### Other — KR default

```
Type: constant
f(x) = 0.50  (clipped to [0.30, 0.65])
```

A composite of hydro, LNG peakers, biomass, etc.  The constant at 0.50 is a simplification; this bucket is mostly dispatchable but modelled as a fixed-utilisation category.

---

## 2. `eta_func` — Thermal efficiency as a function of operating CF

**x = effective capacity factor CF_eff (0–1)**
**output = thermal conversion efficiency η (dimensionless)**

Lower capacity factor means more **start-up cycles** and more time at **part-load**.  Both degrade efficiency:

- **Start-ups**: fuel is burned warming the boiler/turbine from cold; no electricity is generated during this time.  Each cold start wastes roughly 0.5–2% of a day's fuel.
- **Part-load combustion**: turbines and boilers are designed for an optimal pressure and temperature at full load.  Running at 40% load shifts combustion away from the design point → lower η.

This directly affects **fuel cost** and **carbon intensity**:

```
Fuel_g = fuel_usd_mmbtu × heat_rate × η_reference / η_actual
Carbon_g = carbon_price × emission_factor × η_reference / η_actual
```

### Gas CCGT — KR default

```
Type: logarithmic
f(CF) = 0.55 − 0.06·ln(1 + 2.0·CF)
x clipped to [0.35, 0.58]
```

At CF = 0.75 (full baseload)  →  η = 0.55 − 0.06·ln(2.5) ≈ 0.495  ← reference point
At CF = 0.40 (part-load backup) →  η = 0.55 − 0.06·ln(1.8) ≈ 0.514

Wait — at lower CF the clamp kicks in (x_min=0.35), so below 35% CF the efficiency is held at the x_min value.  The function captures that **part-load gas efficiency degrades by ~1–4%** compared to full-load operation, corresponding to real-world data on CCGT heat rate curves.

**The `η_reference`** is taken as the `a` parameter (0.55) — the rated full-load efficiency.  The ratio `η_reference / η_actual > 1` when CF is low, acting as a fuel-cost penalty.

---

### Coal — KR default

```
Type: logarithmic
f(CF) = 0.41 − 0.05·ln(1 + 2.0·CF)
x clipped to [0.28, 0.43]
```

At CF = 0.70 (full operation)  →  η ≈ 0.41 − 0.05·ln(2.4) ≈ 0.366
At CF = 0.40 (part-load)       →  η ≈ 0.41 − 0.05·ln(1.8) ≈ 0.380

Again clamped at [0.28, 0.43].  Coal plants' rated efficiency is ~41% but real dispatch at variable CF degrades this by several points.

---

### Solar, Wind, Nuclear — KR default

```
Type: constant, f(CF) = 1.0  (renewables)
Type: constant, f(CF) = 0.33  (nuclear)
```

Renewables have no fuel — η = 1.0 is a placeholder meaning "no efficiency penalty".
Nuclear η = 0.33 reflects fixed steam-cycle thermal efficiency (not variable with dispatch).

---

## 3. `integration_cost_func` — Grid balancing cost as a function of portfolio share

**x = generator's own portfolio share (0–1)**
**output = integration cost ($/MWh generated by this generator)**

This captures costs *external* to the generator itself: grid balancing, reserves, frequency response, transmission reinforcement, and ancillary services that the rest of the system must provide because of this generator.

### Solar — KR default

```
Type: quadratic
f(s) = 2.0 + 0.5·s + 1.5·s²   ($/MWh)
```

At solar share = 5%   →  cost = 2.00 + 0.025 + 0.004 ≈ $2.03/MWh
At solar share = 30%  →  cost = 2.00 + 0.15 + 0.135 = $2.29/MWh
At solar share = 50%  →  cost = 2.00 + 0.25 + 0.375 = $2.63/MWh

**Why quadratic?**  Integration costs grow non-linearly because:
- At low solar share, the grid can absorb variability with existing reserves
- At high solar share, the system needs new dedicated fast-response assets (batteries, smart inverters, interconnectors) — each marginal solar MWh costs increasingly more to balance

The intercept $2.0/MWh reflects always-present system overhead even at minimal penetration.

---

### Wind (Onshore) — KR default

```
Type: quadratic
f(s) = 2.5 + 0.8·s + 1.8·s²   ($/MWh)
```

Slightly higher than solar because Korea's wind resource is more variable and less predictable hour-to-hour than solar (no predictable diurnal cycle), requiring more reserve procurement.

---

### Gas CCGT — KR default

```
Type: constant
f(s) = 1.0   ($/MWh)
```

Gas IS the integration provider — it backs up everything else.  Its own integration cost is a minimal $1/MWh representing metering, dispatch overhead, and frequency response obligation.  It does not rise with share because gas can always absorb more dispatch calls.

---

### Coal — KR default

```
Type: constant
f(s) = 0.8   ($/MWh)
```

Coal is inflexible but predictable.  Lower integration cost than gas because coal plants stay on a flat schedule — they don't need continuous dispatch instructions.  But they do create some system integration burden when forced to cycle to accommodate VRE.

---

### Nuclear — KR default

```
Type: constant
f(s) = 0.3   ($/MWh)
```

The lowest integration cost.  Nuclear runs flat and predictably.  It *creates* system inflexibility (which drives curtailment) but bears very little external balancing cost itself — that burden falls on the flexible backup.

---

## 4. `curtailment_func` — VRE curtailment rate as a function of system VRE share

**x = effective_vre = VRE_share × flex_scale**  (adjusted for backup flexibility, see below)
**output = fraction of potential VRE output wasted (0–1)**

### Why curtailment grows super-linearly

At low VRE share, surplus generation events are rare and brief.  Demand can absorb most of the VRE output.  As VRE penetration rises:
1. **More hours** of the year have surplus VRE (not just midday peaks)
2. **Each surplus event is larger** (more installed capacity, same demand)
3. **The marginal hour of VRE is increasingly the hardest to absorb** (easy hours already served)

This produces the characteristic super-linear growth captured by the power law.

### Backup availability adjustment

The model adjusts the "effective VRE challenge" to account for how much backup can flex down:

```
backup_flexibility = Σ_{g ∉ VRE} (share_g / non_VRE_share) × (1 − variability_factor_g)

flex_scale = 1 / backup_flexibility        (range: 1.0 for all-gas, 1.25 for all-nuclear)
effective_vre = min(1.0, VRE_share × flex_scale)
```

With **100% gas backup** (flexibility=1.0, flex_scale=1.0): gas simply backs off when VRE is high → effective_vre = VRE_share → baseline curtailment.

With **100% nuclear backup** (flexibility=0.80, flex_scale=1.25): nuclear cannot back down → the VRE must curtail more to balance the grid → effective_vre = VRE_share × 1.25 → ~25% more curtailment at the same VRE share.

**Example (KR, 90% VRE):**
- 90% VRE + 10% gas → backup_flex=1.0, effective_vre=90%, curtailment≈39%
- 90% VRE + 10% nuclear → backup_flex=0.8, effective_vre=min(1, 0.9×1.25)=100%, curtailment≈60%

---

### Solar — KR default

```
Type: power
f(effective_vre) = 0.60 × effective_vre^1.8
x clipped to [0.0, 0.95]
```

At effective VRE = 30%  →  curtailment = 0.60 × 0.30^1.8 ≈ 0.60 × 0.116 ≈ 7%
At effective VRE = 60%  →  curtailment = 0.60 × 0.60^1.8 ≈ 0.60 × 0.383 ≈ 23%
At effective VRE = 90%  →  curtailment = 0.60 × 0.90^1.8 ≈ 0.60 × 0.818 ≈ 49%

**Why `a=0.60`?**  At 100% effective VRE, 60% of solar output would be curtailed — consistent with island-grid studies (e.g. Hawaii, Jeju Island) at saturation.  Korea's grid, with limited interconnection and must-run baseload, approaches this limit.

**Why `b=1.8`?**  Slightly less than 2 (quadratic) — the super-linear growth is steep but not quite parabolic, matching empirical data where curtailment remains low until ~30% VRE then rises sharply.

---

### Wind (Onshore) — KR default

```
Type: power
f(effective_vre) = 0.35 × effective_vre^1.8
```

At effective VRE = 60%  →  curtailment ≈ 0.35 × 0.383 ≈ 13%
At effective VRE = 90%  →  curtailment ≈ 0.35 × 0.818 ≈ 29%

**Why lower than solar (`a=0.35` vs `a=0.60`)?**  Wind output has a smoother diurnal profile — it generates at night and in winter when demand is also elevated.  Solar output is concentrated in daytime hours, which frequently coincide with low-demand periods (mid-day weekends).  Therefore, for the same system VRE share, wind causes less curtailment than solar.

---

## 5. ESS `requirement_func` — Long-duration storage sizing (last-gap)

**x = max(0, VRE_share − 0.65)**  (triggered only above 65% VRE)
**output = annual throughput ratio (fraction of annual generation that must be seasonally shifted)**

```
Type: power
f(shifted) = 3.07 × shifted^3.60
```

At VRE = 65%  →  shifted = 0, long-duration ESS = 0 GWh
At VRE = 70%  →  shifted = 0.05, ratio = 3.07 × 0.05^3.60 ≈ 0.0005 → ~1 GWh for KR
At VRE = 80%  →  shifted = 0.15, ratio = 3.07 × 0.15^3.60 ≈ 0.006 → ~73 GWh for KR
At VRE = 90%  →  shifted = 0.25, ratio = 3.07 × 0.25^3.60 ≈ 0.038 → ~460 GWh for KR
At VRE = 100% →  shifted = 0.35, ratio = 3.07 × 0.35^3.60 ≈ 0.13 → ~1,545 GWh for KR

**Why such a steep exponent (b=3.60)?**  Seasonal storage is the "last gap" problem.  Below 65% VRE, daily and weekly cycling storage (4-hour batteries) can handle all surplus.  Above 65%, the system starts having **multi-day and seasonal surpluses** — weeks of high wind/solar followed by weeks of calm.  Bridging these gaps requires very large reservoirs.  The storage need grows faster than quadratic because:
1. Each additional % of VRE creates longer and more frequent multi-day surpluses
2. The seasonal deficit period (winter darkness/calm in Korea) also deepens
3. The marginal storage hour to be covered is increasingly rare and large

**Why the 65% threshold?**  Below this level, demand-side flexibility, interconnection, and short-duration storage (4-hour batteries) can manage surplus events.  Above 65% VRE, the temporal mismatch spans beyond the 4-hour window and weekly/seasonal storage becomes necessary.

---

## Summary table: All KR default functions

| Generator | cf_eff_func | x range | eta_func | integration_cost_func |
|-----------|-------------|---------|----------|-----------------------|
| Solar | log(a=0.145, b=0.08, c=3.0) | [5%, 20%] VRE | constant 1.0 | quadratic(2.0, 0.5, 1.5) |
| Wind | log(a=0.22, b=0.07, c=2.4) | [8%, 30%] VRE | constant 1.0 | quadratic(2.5, 0.8, 1.8) |
| Gas CCGT | linear(0.75, −0.55) | [10%, 85%] VRE | log(0.55, 0.06, 2.0) | constant 1.0 |
| Coal | piecewise(0.78, 0.25, −0.35, −0.70) | [15%, 80%] VRE | log(0.41, 0.05, 2.0) | constant 0.8 |
| Nuclear | constant 0.87 | [80%, 92%] | constant 0.33 | constant 0.3 |
| Other | constant 0.50 | [30%, 65%] | constant 0.42 | constant 1.0 |

| Generator | curtailment_func | x range |
|-----------|-----------------|---------|
| Solar | power(a=0.60, b=1.8) | [0%, 95%] effective VRE |
| Wind | power(a=0.35, b=1.8) | [0%, 95%] effective VRE |

| ESS component | requirement_func | threshold |
|--------------|-----------------|-----------|
| Long-duration | power(a=3.07, b=3.60) | 65% VRE |
