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
