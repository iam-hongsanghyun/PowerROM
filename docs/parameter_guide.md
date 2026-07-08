# PowerROM Parameter Guide

Use the default country profiles for immediate analysis, or override any nested generator and ESS parameter through `custom_params` in `/api/calculate`.

The frontend parameter panel also supports point fitting through `/api/fit` and configuration completeness checks through `/api/validate`.

## Per-generator parameters (Parameters → Generator Parameters)

Every technology field lives in the country profile's generator block and is editable in the
Parameters tab (edits flow through `custom_params`). Cost fields: `capex_usd_kw`,
`opex_fixed_usd_kw_yr`, `opex_var_usd_mwh`, `lifetime_yr`, `fuel_usd_mmbtu`. Dispatch/physical
fields: `emission_factor_tco2_mwh`, `heat_rate_mmbtu_mwh`, `cf_base`, `variability_factor`.

### Ramp limits

- `ramp_up_frac_per_hr`, `ramp_down_frac_per_hr` — the most a unit's output may change between
  adjacent hours, as a **fraction of nameplate capacity per hour**. The dispatch bounds each
  flexible thermal to `output[h] ∈ [output[h-1] − cap·ramp_down, output[h-1] + cap·ramp_up]`.
- **Config-backed defaults** ship in every profile's flexible-thermal blocks: `gas_ccgt` 0.8,
  `coal` 0.5, `other` 0.7 (stylized from unit-flexibility literature). Nuclear runs as flat
  must-run baseload and VRE follows the weather, so neither carries a ramp limit.
- Edit them per country in Parameters, or, from the API/MCP, override per generator with the
  top-level `ramp_up` / `ramp_down` request fields (which win over the profile default).
- At hourly resolution these bind mainly for slow units on the steep evening net-load ramp; tighten
  them to model an inflexible fleet, where the flexibility gap shows up as unserved energy that
  storage/fast peakers must fill.

## Storage (Parameters → ESS)

Both tiers' economics are profile fields, editable in Parameters → ESS: `capex_usd_kwh`,
`lifetime_yr`, `cycles_per_year`, `dod` (depth of discharge), `duration_hr`, and
`round_trip_efficiency` — the fraction of charged energy returned on discharge (config default:
short/intraday 0.85, long/seasonal 0.45). Storage power (GW) is set on the left rail; energy
(GWh) = power × `duration_hr`. The short tier also carries `arbitrage_price_percentile` (default 75)
— the price-percentile window above which it displaces the marginal thermal in reporting mode.

## Synthetic-profile / reliability knobs (config, per country)

These drive the `parametric` (synthetic) dispatch mode and are stored in the profile:

- `latitude` — hemisphere is derived from it (`latitude < 0` ⇒ southern), so seasons and the winter
  VRE-drought land in the right half of the year.
- `wind_onshore.wind_weibull_k` (default 1.8) — Weibull shape of synthetic wind (lower ⇒ higher CV).
- `wind_onshore.wind_ar1_rho` (default 0.93) — hour-to-hour wind persistence (calm/windy spells).
- `vre_drought` block — the injected winter Dunkelflaute that sets the reliability-binding hour:
  `events` (3), `min_duration_hr` (36), `max_duration_hr` (72), `wind_floor` (0.05), `solar_floor`
  (0.15). Deeper/longer/more-frequent droughts raise the firm-capacity and long-storage the
  reliability sizers must add.

Clean-energy subsidy eligibility (`solar`, `wind_onshore`, `wind_offshore`, `nuclear`) is a global
model definition, deliberately broader than the RPS "renewable" set (`solar` + `wind`): clean =
renewable + nuclear.
