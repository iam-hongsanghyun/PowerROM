# Hourly Profile Data

PowerROM can run hourly dispatch from either seeded parametric profiles or actual hourly data.

To add actual data for a country, place CSV files under:

```text
backend/data/hourly/<COUNTRY>/<YEAR>.csv
```

Example:

```text
backend/data/hourly/KR/2024.csv
```

Each CSV must contain exactly 8760 rows and these columns:

| Column | Meaning |
| --- | --- |
| `demand_norm` | Hourly demand shape, normalized by the loader to mean 1.0 |
| `solar_cf` | Hourly solar capacity factor, clipped to 0-1 |
| `wind_cf` | Hourly onshore wind capacity factor, clipped to 0-1 |

When `dispatch_mode` is `data`, the API loads the requested `weather_years` from this directory.
If no matching files are available, it falls back to the reproducible parametric synthesizer and
adds a data-quality note in the response.
