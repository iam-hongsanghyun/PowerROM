# ROM-Power — Model Description

*Rough-Order-of-Magnitude Power Sector LCOE Model*

---

## Overview

ROM-Power computes the **system-level Levelised Cost of Electricity (LCOE)** for a user-defined mix of generation technologies. Every cost driver — capacity factor degradation, part-load thermal efficiency, grid integration cost, curtailment, and energy storage — is expressed as a **configurable mathematical function** whose type, parameters, and input variable the user can freely edit in real time.

The model is intentionally simplified. It trades detail for speed and transparency: a single Python function call recomputes the full system LCOE and sweeps the curve from 0% to 100% VRE in under a second. All assumptions are visible and editable.

---

## 1. Generator LCOE

For each generator *g* with normalised portfolio share *s_g* the per-unit LCOE ($/MWh delivered) is:

```
LCOE_g = CAPEX_g + FixedOPEX_g + VariableOPEX_g + Fuel_g + Carbon_g + Integration_g
```

### 1.1 CAPEX component

Annualised capital cost spread over delivered energy:

```
CAPEX_g = capex_usd_kw × CRF(r, T_g) / (CF_eff_g × 8760) × 1000
```

where the **Capital Recovery Factor** is:

```
CRF(r, T) = r(1+r)^T / ((1+r)^T − 1)
```

- `r` — country discount rate (e.g. 0.05)
- `T_g` — generator economic lifetime (years)
- `CF_eff_g` — effective capacity factor ← output of `cf_eff_func`

### 1.2 Fixed O&M component

```
FixedOPEX_g = opex_fixed_usd_kw_yr / (CF_eff_g × 8760) × 1000
```

### 1.3 Fuel component (thermal generators only)

Fuel cost is penalised when part-load operation degrades thermal efficiency:

```
Fuel_g = fuel_usd_mmbtu × heat_rate_mmbtu_mwh × η_penalty_g
η_penalty_g = η_reference_g / η_g
```

- `η_g` — actual efficiency at current operating CF ← output of `eta_func`
- `η_reference_g` — rated full-load efficiency (the `a` parameter for constant `eta_func`)

### 1.4 Carbon component

```
Carbon_g = carbon_price × emission_factor_tco2_mwh × η_penalty_g
```

### 1.5 Integration cost component

```
Integration_g = integration_cost_func_g(x)
```

where *x* defaults to the generator's own portfolio share *s_g* but can be reassigned to any runtime variable via the **Input Variable** selector (see Section 3).

### 1.6 System LCOE

```
SystemLCOE = Σ_g (s_g × LCOE_g) + ESS_LCOE
```

---

## 2. Function Catalog

All behavioural relationships in the model are expressed through a library of **eight** parametric functions. Each function maps one input variable *x* to one output value. The output is optionally clipped to the range `[x_min, x_max]` when those bounds are specified.

Every function's **type**, **parameters**, and **input variable** are user-editable in the Parameters tab via the popup editor. Parameter changes update the live preview chart instantly.

---

### 2.1 Constant

```
f(x) = a
```

| Parameter | Meaning |
|-----------|---------|
| `a` | Constant output for any input |

Returns the same value regardless of *x*. Used when a property is assumed independent of operating conditions (e.g. nuclear CF, renewable η).

---

### 2.2 Linear

```
f(x) = a + b·x
```

| Parameter | Meaning |
|-----------|---------|
| `a` | Intercept — value at x = 0 |
| `b` | Slope — rate of change per unit x (negative for declining relationships) |

Straight-line relationship. Used for gas CCGT capacity factor declining with VRE penetration.

---

### 2.3 Logarithmic

```
f(x) = a − b·ln(1 + c·x)
```

| Parameter | Meaning |
|-----------|---------|
| `a` | Ceiling — value at x = 0 |
| `b` | Amplitude of decline (larger = steeper total drop) |
| `c` | Curvature — how sharply the initial drop occurs |

Captures diminishing losses: rapid decline at low *x*, flattening at high *x*. Used for VRE effective CF degradation and thermal efficiency part-load curves.

---

### 2.4 Quadratic

```
f(x) = a + b·x + c·x²
```

| Parameter | Meaning |
|-----------|---------|
| `a` | Intercept |
| `b` | Linear coefficient (slope at x = 0) |
| `c` | Curvature: positive → U-shape, negative → inverted-U |

Used for integration costs that grow non-linearly with a generator's share.

---

### 2.5 Exponential

```
f(x) = a · e^(b·x)
```

| Parameter | Meaning |
|-----------|---------|
| `a` | Scale factor — value at x = 0 |
| `b` | Growth rate: positive → growth, negative → decay |

Available for strongly non-linear cost curves.

---

### 2.6 Power

```
f(x) = a · x^b        (x ≥ 0)
```

| Parameter | Meaning |
|-----------|---------|
| `a` | Scale — value when x = 1 |
| `b` | Exponent: b < 1 = sub-linear, b = 1 = linear, b > 1 = super-linear (accelerating) |

Used for curtailment functions (super-linear growth with VRE share) and for the long-duration ESS requirement (very steep onset above the VRE threshold).

---

### 2.7 Piecewise Linear

```
f(x) = intercept + slope_before · x                                              if x ≤ threshold
f(x) = intercept + slope_before · threshold + slope_after · (x − threshold)      if x > threshold
```

| Parameter | Meaning |
|-----------|---------|
| `intercept` | Value at x = 0 |
| `threshold` | x-value where the slope changes |
| `slope_before` | Rate of change for x ≤ threshold |
| `slope_after` | Rate of change for x > threshold |

Used for coal capacity factor: gentle merit-order displacement at low VRE, then steeper displacement once cheaper generators are already pushed out.

---

### 2.8 Multilinear

```
f = intercept + β₁·x₁ + β₂·x₂ + β₃·x₃ + …
```

| Parameter | Meaning |
|-----------|---------|
| `intercept` | Base value when all predictors = 0 |
| `<variable_name>` | Slope (β) for that predictor variable |

**Multiple input variables simultaneously.** Unlike all other function types which take a single *x*, multilinear takes a named set of predictors from the runtime context. The parameter dict encodes both which variables to use and their slopes:

```json
{
  "type": "multilinear",
  "params": {
    "intercept": 2.0,
    "vre_share": 0.5,
    "own_share": 0.3
  }
}
```

This evaluates as `2.0 + 0.5 × vre_share + 0.3 × own_share` at runtime.

**Available predictor variables:**

| Variable name | Runtime value |
|---------------|---------------|
| `vre_share` | System-wide VRE fraction (0–1) |
| `own_share` | This generator's portfolio share (0–1) |
| `cf_eff` | This generator's effective CF (computed earlier in the same call) |
| `non_vre_share` | 1 − VRE share |

**In the UI:** Select `multilinear` as Function Type, set the intercept slider, then click *"+ Add predictor variable…"* to add each predictor and set its slope. Predictors can be removed with ✕.

**Use case example — integration cost rising with both VRE share and own share:**
```
Integration = 1.5 + 4.0 × vre_share + 2.0 × own_share
```
At 60% VRE and 20% own share: `1.5 + 4.0×0.6 + 2.0×0.2 = 4.3 $/MWh`

---

## 3. Input Variable (x) Selection

For all function types **except multilinear**, a single scalar *x* is passed to the function. Which runtime value that is defaults to the physically natural choice for each function, but can be overridden via the **Input Variable** dropdown in the popup editor:

| Variable name | Description | Natural default for |
|---------------|-------------|---------------------|
| `vre_share` | System VRE fraction (0–1) | `cf_eff_func`, `curtailment_func` |
| `own_share` | This generator's portfolio share (0–1) | `integration_cost_func` |
| `cf_eff` | This generator's effective CF (result of `cf_eff_func`) | `eta_func` |
| `non_vre_share` | 1 − VRE share | — (override option) |

**Example:** Setting `cf_eff_func.x_variable = "own_share"` makes a generator's CF degradation depend on its own portfolio share rather than on total VRE. A baseload plant that runs less when it is a larger fraction of the mix would use this.

The backend resolves the correct variable at evaluation time; if the variable is absent from the context (e.g. `cf_eff` is requested but the generator has no thermal function), the default *x* is used as a fallback.

---

## 4. Per-Generator Configurable Functions

Each generator carries four functions. All are editable in the Parameters → Generator Functions section by clicking the generator in the sidebar list.

### 4.1 `cf_eff_func` — Effective Capacity Factor

- **Default input x**: system VRE share
- **Output**: effective capacity factor (0–1)
- **Used for**: CAPEX normalisation, Fixed O&M normalisation, input to `eta_func`

As VRE share rises, dispatchable generators are dispatched less (lower CF) and VRE generators face curtailment (also lower effective CF).

**Typical shapes by technology:**

| Generator | Shape | Intuition |
|-----------|-------|-----------|
| Solar, Wind | Logarithmic | Curtailment and profile mismatch; diminishing losses |
| Gas CCGT | Linear (declining) | Dispatched proportionally to fill residual demand |
| Coal | Piecewise linear | Gentle displacement first, steep once gas is already crowded out |
| Nuclear | Constant | Must-run baseload; policy-driven utilisation |
| Other | Constant | Mixed dispatchable bucket |

---

### 4.2 `eta_func` — Thermal Efficiency

- **Default input x**: this generator's effective CF (`cf_eff`)
- **Output**: thermal conversion efficiency η
- **Used for**: `η_penalty = η_reference / η_actual` applied to Fuel and Carbon cost

Lower CF means more start-up cycles and more part-load operation, both of which degrade combustion efficiency. For renewables η is set to 1.0 (no penalty).

**Typical shapes:**

| Generator | Shape | Note |
|-----------|-------|------|
| Gas CCGT | Logarithmic of CF | Peaks near rated CF; degrades 2–4% at part-load |
| Coal | Logarithmic of CF | Shallower degradation than gas |
| Nuclear, Renewables | Constant | No thermodynamic part-load effect in this model |

---

### 4.3 `integration_cost_func` — Grid Integration Cost

- **Default input x**: generator's own portfolio share
- **Output**: integration cost ($/MWh generated)
- **Used for**: added directly to the generator's LCOE component

Captures grid-level costs not in the generator's own cost curve: balancing, ancillary services, reserves, transmission.

**Typical shapes:**

| Generator | Shape | Intuition |
|-----------|-------|-----------|
| Solar, Wind | Quadratic (rising) | Non-linear balancing burden at high penetration |
| Gas CCGT | Constant (low) | Already provides balancing; minimal external cost |
| Coal, Nuclear | Constant (very low) | Inflexible but predictable; low ancillary burden |

---

### 4.4 `curtailment_func` — VRE Curtailment Rate *(VRE generators only)*

- **Default input x**: effective VRE (= VRE share adjusted for backup flexibility, see Section 6)
- **Output**: fraction of potential VRE output curtailed (0–1)
- **Used for**: curtailment metrics and short-duration ESS throughput sizing

Only solar and wind carry this function. Dispatchable generators do not curtail.

**Default calibration (power law `a·x^b`):**

| Country | Generator | a | b |
|---------|-----------|---|---|
| KR | Solar | 0.60 | 1.8 |
| KR | Wind | 0.35 | 1.8 |
| AU | Solar | 0.55 | 1.8 |
| JP | Solar | 0.65 | 1.8 |

---

## 5. Generator Scalar Parameters

| Parameter | Unit | Description |
|-----------|------|-------------|
| `capex_usd_kw` | USD/kW | Overnight construction cost |
| `opex_fixed_usd_kw_yr` | USD/kW/yr | Annual fixed operations & maintenance |
| `opex_var_usd_mwh` | USD/MWh | Variable O&M (wear, consumables) |
| `lifetime_yr` | years | Economic project lifetime |
| `emission_factor_tco2_mwh` | tCO₂/MWh | Direct combustion emission intensity |
| `fuel_usd_mmbtu` | USD/MMBtu | Fuel commodity price |
| `heat_rate_mmbtu_mwh` | MMBtu/MWh | Rated heat-to-electricity conversion |
| `cf_base` | 0–1 | Rated capacity factor (no system effects) |
| `variability_factor` | 0–1 | Contribution to system variability (0 = fully dispatchable, 1 = fully intermittent) |

### `variability_factor` detail

This index captures how much a generator *adds* to system variability per unit of portfolio share. It is currently used in the **backup flexibility calculation** for curtailment (see Section 6).

| Generator | Default | Rationale |
|-----------|---------|-----------|
| Solar | 1.00 | Maximum intermittency; zero inertia |
| Wind (onshore) | 0.85 | Smoother than solar; non-dispatchable |
| Nuclear | 0.20 | Inflexible baseload creates residual load variability |
| Coal | 0.10 | Slow-ramping; partially dispatchable |
| Gas CCGT | 0.00 | Fully dispatchable — IS the buffer |
| Other | 0.30 | Mixed bucket |

---

## 6. Curtailment Model

### 6.1 Backup Flexibility Adjustment

Curtailment depends not only on how much VRE is in the system, but on how much the backup can flex *down*. A grid backed by gas (which can back off instantly) absorbs the same VRE with far less curtailment than one backed by must-run nuclear.

```
backup_flexibility = Σ_{g ∉ VRE} (share_g / non_VRE_share) × (1 − variability_factor_g)

flex_scale = 1 / max(backup_flexibility, 0.5)       [cap at 2× amplification]
effective_vre = min(1.0, VRE_share × flex_scale)
```

- **Gas-only backup** (flexibility = 1.0): effective_vre = VRE_share → baseline curtailment
- **Nuclear-only backup** (flexibility = 0.80): effective_vre = VRE_share × 1.25 → ~25% more curtailment

### 6.2 Per-Generator Curtailment

Each VRE generator's curtailment is evaluated at `effective_vre`:

```
cr_g = curtailment_func_g(effective_vre)
```

### 6.3 System Curtailment Metrics

```
curtailment_rate = Σ_{g ∈ VRE} (share_g × cr_g) / VRE_share     [share-weighted average]
curtailed_TWh   = annual_TWh × Σ_{g ∈ VRE} share_g × cr_g
```

---

## 7. Energy Storage System (ESS)

ESS is split into two physically distinct components that size independently.

### 7.1 Short-Duration Storage (4-hour batteries)

Sized by how much curtailed VRE can be absorbed:

```
throughput_GWh = Σ_{g ∈ VRE} share_g × annual_TWh × 1000 × cr_g × absorption_fraction_g
net_throughput  = max(throughput − EV_offset, 0)
battery_GWh    = net_throughput / (cycles_per_year × dod)
```

**Editable parameters:**

| Parameter | Description |
|-----------|-------------|
| `solar_absorption_fraction` | Fraction of curtailed solar that ESS can absorb (default 0.55) |
| `wind_onshore_absorption_fraction` | Fraction of curtailed wind absorbed (default 0.30) |
| `cycles_per_year` | Annual full cycles (default 300 — daily cycling) |
| `dod` | Depth of discharge (default 0.85) |
| `duration_hr` | Discharge duration in hours (default 4) |
| `ev_offset_gwh_per_unit` | EV fleet flexibility that displaces dedicated ESS |

**LCOE contribution:**

```
short_lcoe [$/MWh] = capex_usd_kwh × CRF(r, T) × battery_GWh / annual_TWh
```

### 7.2 Long-Duration Storage (seasonal, 168-hour)

The "last gap" — activated only above a VRE share threshold where multi-day surpluses exceed short-duration storage capacity:

```
shifted     = max(0, VRE_share − threshold)     [threshold = 0.65 by default]
long_ratio  = a × shifted^b                     [default: a = 3.07, b = 3.60]
battery_GWh = long_ratio × annual_TWh × 1000 / (cycles × dod)
```

The exponent b = 3.60 produces steep onset: negligible at 70% VRE, large at 90%+.

**`requirement_func` is editable** — type (power/linear/etc.), `a`, `b`, and `threshold` can all be changed via the ESS section of the Parameters tab.

---

## 8. Curve Data

The model sweeps VRE share from 0% to 100% in 1-percentage-point steps, holding the user's solar:wind ratio and non-VRE generator weights constant. This produces all the LCOE, emissions, and storage curves visible in the Profile Analysis tab.

At each curve point, the **actual x-axis VRE share** (not the normalised portfolio share) is passed to all functions as `vre_share_override`, ensuring the curves represent physically meaningful scenarios even for pure-VRE portfolios.

---

## 9. User Editability Summary

Everything in the table below can be changed in the **Parameters tab**. Function types, parameters, and input variables can be changed via the popup modal (click any generator in the Generator Functions sidebar).

| Component | Editable scalars | Editable functions |
|-----------|------------------|--------------------|
| Country | `annual_generation_twh`, `discount_rate` | — |
| Each generator | `capex`, `opex_fixed`, `opex_var`, `lifetime`, `emission_factor`, `fuel_cost`, `heat_rate`, `cf_base`, `variability_factor` | `cf_eff_func`, `eta_func`, `integration_cost_func`, `curtailment_func` |
| Short-dur ESS | `capex_usd_kwh`, `lifetime_yr`, `cycles_per_year`, `dod`, `duration_hr`, `ev_offset_gwh_per_unit`, `solar_absorption_fraction`, `wind_onshore_absorption_fraction` | — |
| Long-dur ESS | `capex_usd_kwh`, `lifetime_yr`, `cycles_per_year`, `dod`, `duration_hr`, `threshold` | `requirement_func` |

**For each function** the user can change:
- **Type** — constant, linear, logarithmic, quadratic, exponential, power, piecewise, or **multilinear**
- **Parameters** — via labelled sliders with hints; the live preview chart updates in real time
- **Input variable (x)** — which runtime quantity feeds into the function (VRE Share, Own Share, CF_eff, 1−VRE Share); not applicable for multilinear (which specifies predictors by name in its params)
- **x_min / x_max** — output clipping bounds
- **Source** — citation or note for the calibration

---

## 10. Data Sources (default profiles)

- IEA World Energy Outlook 2024
- IRENA Renewable Power Generation Costs 2024
- OECD/NEA Nuclear Energy Cost Data (stylized integration defaults)
- Country-specific fuel price data (KR: KOGAS LNG contracts; AU: domestic spot; JP: JKM LNG)
- Curtailment calibration: Jeju Island empirical data; Hawaii HECO curtailment reports; IEA VRE Integration Cost studies 2023
