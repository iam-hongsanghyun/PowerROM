# Hourly country profiles (`data` dispatch mode)

When a request uses `dispatch_mode="data"`, the engine loads real hourly profiles
from this directory instead of the parametric synthesizer:

```
backend/data/hourly/{COUNTRY}/{year}.csv
```

Each CSV has **exactly three columns and 8760 rows**:

| column | meaning | units |
|--------|---------|-------|
| `demand_norm` | hourly electricity demand | any positive unit — renormalised to mean 1.0 on load |
| `solar_cf` | hourly solar capacity factor | 0–1 |
| `wind_cf` | hourly onshore-wind capacity factor | 0–1 |

Multiple years per country are supported — the ensemble draws across them
(`ensemble.method = "multiyear"`), which is what turns the p10–p90 bands into a real
weather-uncertainty range. If a country/year file is missing, `data` mode falls back
to the seeded parametric synthesizer, so the app keeps working while data is sourced.

## Getting real data

Two free sources cover most countries; both need a (free) API token you supply at
runtime — nothing is committed to the repo.

- **Solar / wind capacity factors** — [Renewables.ninja](https://renewables.ninja)
  (MERRA-2 / ERA5 reanalysis). Register → API token.
- **Electricity demand** — region-specific: ENTSO-E Transparency (EU), EIA (US),
  OpenNEM (AU), KPX (KR). Export an hourly load series to a one-column CSV.

## Ingesting a profile

`ingest.py` fetches capacity factors, reads a demand CSV, normalises to the format
above, validates, and writes the file:

```bash
python -m backend.data.hourly.ingest \
    --country KR --year 2023 --lat 37.5 --lon 127.0 \
    --ninja-token "$NINJA_TOKEN" --demand-csv kr_2023_load_mw.csv
```

Then select **data** dispatch mode in the control panel (or `dispatch_mode="data"`
in the API) to use it.
