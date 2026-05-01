# ROM-Power — Model Description

*Rough-Order-of-Magnitude Power Sector LCOE Model*

---

## Overview

ROM-Power computes the **system-level Levelised Cost of Electricity (LCOE)** for a user-defined mix of generation technologies.  Every cost driver — capacity factor degradation, part-load thermal efficiency, grid integration cost, curtailment, and energy storage — is expressed as a **configurable mathematical function** whose parameters the user can freely edit.  Changing a parameter updates the model in real time.

---

## 1. Generator LCOE

For each generator *g* with portfolio share *s_g* the per-unit LCOE ($/MWh delivered) is:

```
LCOE_g = CAPEX_g + FixedOPEX_g + VariableOPEX_g + Fuel_g + Carbon_g + Integration_g
```

### 1.1 CAPEX component

Annualised capital cost spread over delivered energy:

```
CAPEX_g = capex_usd_kw × CRF(r, T_g) / (CF_eff_g × 8760) × 1000
```

where `CRF(r, T)` is the **Capital Recovery Factor**:

```
CRF(r, T) = r(1+r)^T / ((1+r)^T − 1)
```

- `r` — country discount rate (e.g. 0.05)
- `T_g` — generator lifetime in years
- `CF_eff_g` — effective (system-adjusted) capacity factor  ← from `cf_eff_func`

### 1.2 Fixed O&M component

```
FixedOPEX_g = opex_fixed_usd_kw_yr / (CF_eff_g × 8760) × 1000
```

### 1.3 Fuel component (thermal generators only)

Fuel cost is penalised for part-load efficiency loss:

```
Fuel_g = fuel_usd_mmbtu × heat_rate_mmbtu_mwh × η_penalty_g
η_penalty_g = η_reference_g / η_g
```

- `η_g` — actual thermal efficiency at current CF  ← from `eta_func`
- `η_reference_g` — rated full-load efficiency (= `a` parameter of `eta_func` for constant functions)

### 1.4 Carbon component

```
Carbon_g = carbon_price × emission_factor_tco2_mwh × η_penalty_g
```

### 1.5 Integration cost component

```
Integration_g = integration_cost_func_g(s_g)
```

This represents grid balancing costs ($/MWh) as a function of the generator's own portfolio share *s_g*.

### 1.6 System LCOE

```
SystemLCOE = Σ_g (s_g × LCOE_g) + ESS_LCOE
```

---

## 2. Function Catalog

All behavioural relationships in the model are expressed through a library of seven parametric functions.  Every function is evaluated against an input variable `x` and clipped to the range `[x_min, x_max]` when those bounds are specified.

### 2.1 Constant

```
f(x) = a
```

Parameters: **a**

Returns the same value for any input.  Used when a generator's property is assumed independent of operating conditions (e.g. nuclear CF, renewable thermal efficiency).

| Parameter | Meaning |
|-----------|---------|
| `a` | Constant output value |

---

### 2.2 Linear

```
f(x) = a + b·x
```

Parameters: **a**, **b**

Straight-line relationship.  Used for gas CCGT capacity factor declining with VRE penetration (as gas is dispatched less).

| Parameter | Meaning |
|-----------|---------|
| `a` | Intercept — value when x = 0 |
| `b` | Slope — rate of change per unit x |

---

### 2.3 Logarithmic

```
f(x) = a − b·ln(1 + c·x)
```

Parameters: **a**, **b**, **c**

Captures diminishing returns: rapid decline at low x, flattening at high x.  Used for VRE effective CF — curtailment and integration losses accelerate at first, then saturate.

| Parameter | Meaning |
|-----------|---------|
| `a` | Value at x = 0 (baseline) |
| `b` | Amplitude of decline |
| `c` | Curvature — how quickly the decline accelerates |

---

### 2.4 Quadratic

```
f(x) = a + b·x + c·x²
```

Parameters: **a**, **b**, **c**

Parabolic.  Used for integration costs that grow non-linearly with a generator's share (e.g. balancing overhead rises faster than share).

| Parameter | Meaning |
|-----------|---------|
| `a` | Intercept |
| `b` | Linear coefficient |
| `c` | Quadratic coefficient (positive → U-shape; negative → inverted-U) |

---

### 2.5 Exponential

```
f(x) = a · e^(b·x)
```

Parameters: **a**, **b**

Exponential growth or decay.  Available for strongly non-linear cost curves (rarely used in default profiles but supported for custom scenarios).

| Parameter | Meaning |
|-----------|---------|
| `a` | Scale factor (value at x = 0) |
| `b` | Growth rate (positive → growth; negative → decay) |

---

### 2.6 Power

```
f(x) = a · x^b        (x ≥ 0)
```

Parameters: **a**, **b**

Power law.  Used for curtailment functions (curtailment grows super-linearly with VRE share) and for the long-duration ESS requirement (grows very steeply above the VRE threshold).

| Parameter | Meaning |
|-----------|---------|
| `a` | Scale — value when x = 1 |
| `b` | Exponent — 1 = linear, >1 = accelerating, <1 = decelerating |

---

### 2.7 Piecewise Linear

```
f(x) = intercept + slope_before · x                                           if x ≤ threshold
f(x) = intercept + slope_before · threshold + slope_after · (x − threshold)   if x > threshold
```

Parameters: **intercept**, **threshold**, **slope_before**, **slope_after**

Two-segment linear function.  Used for coal capacity factor: utilisation declines gently at low VRE penetration (merit-order displacement), then sharply once VRE dominates.

| Parameter | Meaning |
|-----------|---------|
| `intercept` | Value at x = 0 |
| `threshold` | x-value where the slope changes |
| `slope_before` | Rate of change for x ≤ threshold |
| `slope_after` | Rate of change for x > threshold |

---

## 3. Per-Generator Configurable Functions

Each generator carries four functions.  All are user-editable in the Parameters tab.

### 3.1 `cf_eff_func` — Effective Capacity Factor

- **Input x**: system VRE share (0–1)
- **Output**: effective capacity factor of this generator (0–1)
- **Used for**: CAPEX normalisation, Fixed O&M normalisation, downstream η calculation

As VRE share rises, dispatchable generators run less (load-following) and VRE generators face curtailment losses — both reduce their effective CF.

**Typical shapes by technology:**

| Generator | Function | Intuition |
|-----------|----------|-----------|
| Solar, Wind | Logarithmic: starts at `cf_base`, falls with VRE | Curtailment and profile mismatch |
| Gas CCGT | Linear: high CF at low VRE, declining | Backs up VRE variability |
| Coal | Piecewise linear: gentle then steep decline | Merit-order displacement |
| Nuclear | Constant (policy-managed utilisation) | Baseload, rarely curtailed |
| Other | Constant | Default dispatchable bucket |

---

### 3.2 `eta_func` — Thermal Efficiency

- **Input x**: effective capacity factor `CF_eff`
- **Output**: actual thermal efficiency η (dimensionless)
- **Used for**: fuel cost penalty, carbon intensity penalty

Part-load operation degrades heat-to-electricity conversion.  For renewables η = 1.0 (constant; no fuel combustion).

**Typical shapes:**

| Generator | Function | Note |
|-----------|----------|------|
| Gas CCGT | Logarithmic of CF | Efficiency peaks near rated CF, falls at part-load |
| Coal | Logarithmic of CF | Similar but shallower |
| Nuclear, Renewables | Constant = 1.0 (or fixed η) | Not thermodynamic in this model context |

---

### 3.3 `integration_cost_func` — Grid Integration Cost

- **Input x**: generator's own portfolio share `s_g` (0–1)
- **Output**: incremental integration cost ($/MWh)
- **Used for**: added directly to generator LCOE

Captures grid-level costs not reflected in generator-level cost curves: balancing, ancillary services, transmission, and reserves.

**Typical shapes:**

| Generator | Function | Intuition |
|-----------|----------|-----------|
| Solar | Quadratic — steep at high shares | Profile mismatch forces expensive balancing |
| Wind | Quadratic — moderate | Less peaky than solar |
| Gas CCGT | Constant ~1.0 | Already the balancing provider |
| Coal, Nuclear | Constant, low | Inflexible but predictable |

---

### 3.4 `curtailment_func` — VRE Curtailment Rate *(VRE generators only)*

- **Input x**: system VRE share (0–1)
- **Output**: fraction of potential output curtailed (0–1)
- **Used for**: curtailment metrics; short-duration ESS throughput sizing

Curtailment occurs when instantaneous VRE generation exceeds demand and the grid cannot absorb it.  It grows super-linearly: at low VRE penetration little is wasted, but as VRE dominates the grid, curtailment rises rapidly.

**Default calibration (power law `a·x^b`):**

| Country | Generator | a | b | Interpretation |
|---------|-----------|---|---|----------------|
| KR | Solar | 0.60 | 1.8 | 60% curtailment rate at 100% VRE |
| KR | Wind | 0.35 | 1.8 | 35% curtailment at 100% VRE |
| AU | Solar | 0.55 | 1.8 | Better resource → slightly lower |
| JP | Solar | 0.65 | 1.8 | Island grid → higher curtailment |

---

## 4. Generator Scalar Parameters

Beyond functions, each generator has fixed scalar parameters:

| Parameter | Unit | Description |
|-----------|------|-------------|
| `capex_usd_kw` | USD/kW | Overnight construction cost |
| `opex_fixed_usd_kw_yr` | USD/kW/yr | Annual fixed operations & maintenance |
| `opex_var_usd_mwh` | USD/MWh | Variable O&M (wear, consumables) |
| `lifetime_yr` | years | Project economic lifetime |
| `emission_factor_tco2_mwh` | tCO₂/MWh | Direct combustion emission intensity |
| `fuel_usd_mmbtu` | USD/MMBtu | Fuel commodity price |
| `heat_rate_mmbtu_mwh` | MMBtu/MWh | Rated heat-to-electricity conversion |
| `cf_base` | 0–1 | Rated capacity factor (no system effects) |
| `variability_factor` | 0–1 | Contribution to system ESS need (0 = fully dispatchable; 1 = fully intermittent) |

### `variability_factor` detail

This dimensionless index captures how much a generator *adds* to system variability per unit of portfolio share.  It is separate from the curtailment-driven ESS model and is available as a user-editable override.

| Generator | Default | Rationale |
|-----------|---------|-----------|
| Solar | 1.00 | Maximum intermittency; zero inertia |
| Wind (onshore) | 0.85 | Smoother than solar; non-dispatchable |
| Nuclear | 0.20 | Inflexible baseload creates residual load variability |
| Coal | 0.10 | Slow-ramping; partially dispatchable |
| Gas CCGT | 0.00 | Fully dispatchable — IS the buffer; adds no ESS need |
| Other | 0.30 | Mixed bucket |

---

## 5. Energy Storage System (ESS)

ESS is split into two physically distinct components:

### 5.1 Short-Duration Storage (4-hour batteries)

Sized by how much curtailed VRE can be absorbed by storage:

```
throughput_GWh = Σ_{g ∈ VRE} share_g × annual_TWh × 1000 × curtailment_rate_g × absorption_fraction_g
net_throughput  = max(throughput − EV_offset, 0)
battery_GWh    = net_throughput / (cycles_per_year × depth_of_discharge)
```

**Key parameters (editable):**

| Parameter | Description |
|-----------|-------------|
| `solar_absorption_fraction` | Fraction of curtailed solar that ESS can absorb (default 0.55) |
| `wind_onshore_absorption_fraction` | Fraction of curtailed wind absorbed (default 0.30) |
| `cycles_per_year` | Annual full charge-discharge cycles (default 300) |
| `dod` | Usable depth of discharge (default 0.85) |
| `duration_hr` | Discharge duration in hours (4 hr = daily cycling) |
| `ev_offset_gwh_per_unit` | EV fleet charging flexibility that displaces dedicated ESS |

**Short-duration LCOE contribution:**

```
short_lcoe [$/MWh] = capex_usd_kwh × CRF(r, T) × battery_GWh / annual_TWh
```

---

### 5.2 Long-Duration Storage (seasonal, 168-hour)

The "last gap" — seasonal/multi-day storage that becomes critical only at very high VRE penetration.  Sized by a power law in the shifted VRE share:

```
shifted      = max(0, VRE_share − threshold)          (threshold = 0.65 by default)
long_ratio   = a × shifted^b                          (default a=3.07, b=3.60)
battery_GWh  = long_ratio × annual_TWh × 1000 / (cycles × dod)
```

The exponent b=3.60 means storage need grows *very steeply* above the 65% threshold — at 70% VRE storage is negligible (~1 GWh for KR), but at 90% it reaches ~460 GWh and at 100% ~1,545 GWh.

**`requirement_func` parameters (editable):**

| Parameter | Meaning |
|-----------|---------|
| `a` | Scale of long-duration need at shifted x = 1 |
| `b` | Super-linearity of last-gap; higher = steeper onset |
| `threshold` | VRE share below which long-duration storage is negligible |

---

## 6. Curtailment Metrics

System-level curtailment is aggregated across VRE generators:

```
curtailment_rate = Σ_{g ∈ VRE} (share_g × curtailment_rate_g) / VRE_share
curtailed_TWh   = annual_TWh × Σ_{g ∈ VRE} share_g × curtailment_rate_g
```

where each `curtailment_rate_g = curtailment_func_g(VRE_share)`.

---

## 7. Curve Data

The model sweeps VRE share from 0% to 100% in 1-percentage-point steps, maintaining the user's specified solar:wind ratio and non-VRE generator weights constant.  This produces the LCOE and storage curves visible in the Profile Analysis tab.

At each point x the **actual x-axis VRE share** (not the normalized portfolio share) is passed to all functions, ensuring that the curve represents physically meaningful scenarios.

---

## 8. User Editability Summary

Every number in the table below is a parameter the user can change in the **Parameters tab**, and every function listed can have its type changed (constant, linear, logarithmic, quadratic, exponential, power, or piecewise) and its parameters edited in real time:

| Component | Editable scalars | Editable functions |
|-----------|------------------|--------------------|
| Country | `annual_generation_twh`, `discount_rate` | — |
| Each generator | `capex`, `opex_fixed`, `opex_var`, `lifetime`, `emission_factor`, `fuel_cost`, `heat_rate`, `cf_base`, `variability_factor` | `cf_eff_func`, `eta_func`, `integration_cost_func`, `curtailment_func` |
| Short-dur ESS | `capex_usd_kwh`, `lifetime_yr`, `cycles_per_year`, `dod`, `duration_hr`, `ev_offset_gwh_per_unit`, `solar_absorption_fraction`, `wind_onshore_absorption_fraction` | — |
| Long-dur ESS | `capex_usd_kwh`, `lifetime_yr`, `cycles_per_year`, `dod`, `duration_hr`, `threshold` | `requirement_func` |

---

## 9. Data Sources (default profiles)

- IEA World Energy Outlook 2024
- IRENA Renewable Power Generation Costs 2024
- OECD/NEA Nuclear Energy Cost Data (stylized integration defaults)
- Country-specific fuel price data (KR: KOGAS LNG contracts; AU: domestic spot; JP: JKM LNG)
