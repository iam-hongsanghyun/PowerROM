"""Fetch each country's net fossil-fuel imports from UN Comtrade (energy dependency).

Writes ``backend/data/energy_dependency.json``: per country, the net imported energy (PJ) of
coal, natural gas and oil for the latest reported year. ``build_country_profiles.py`` turns
these into per-generator ``import_fuel_fraction`` values (share of the power sector's fuel burn
met by net imports), replacing the old stylization that ALL gas/coal is imported — which was
wrong for exporters (US, AU, NO, DZ, ...) and for domestic-lignite grids.

Data source
-----------
UN Comtrade public *preview* API (no key, rate-limited), annual HS trade, world-partner totals:

* coal        — HS 2701 (anthracite/bituminous)
* oil         — HS 2709 (crude) + 2710 (refined products; what oil-fired power actually burns)
* natural gas — HS 271111 (LNG) + 271121 (pipeline gas); the 4-digit 2711 heading is NOT used
  because it lumps in LPG and its aggregate rows often carry value but no net weight.

Net weight (kg) converts to energy with IPCC 2006 default net calorific values (GJ/tonne).
Raw API responses are cached (git-ignored) so reruns are offline.

Run:
    python -m backend.data.build_energy_dependency              # all countries in the manifest
    python -m backend.data.build_energy_dependency --countries KR AU
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent
MANIFEST = DATA_DIR / "country_profiles_manifest.json"
OUT_PATH = DATA_DIR / "energy_dependency.json"
CACHE_DIR = DATA_DIR / "comtrade_cache"  # raw responses, git-ignored
M49_CSV = DATA_DIR / "m49_iso3.csv"      # UN Statistics Division M49 ↔ ISO-3166 alpha-3

PREVIEW_URL = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"
THROTTLE_S = 1.0          # polite spacing on the rate-limited public endpoint
YEARS_PREFERRED = (2024, 2023, 2022)  # latest reported year wins, per country

# HS code → (fuel bucket, net calorific value GJ/tonne). NCVs: IPCC 2006 GL Vol.2 Ch.1 Table 1.2
# defaults (other bituminous coal, crude oil, gas/diesel oil as refined basket, natural gas).
HS_FUELS: dict[str, tuple[str, float]] = {
    "2701": ("coal", 25.8),
    "2709": ("oil", 42.3),
    "2710": ("oil", 43.0),
    "271111": ("gas", 48.0),  # LNG
    "271121": ("gas", 48.0),  # pipeline natural gas
}
FUELS = ("coal", "gas", "oil")
KG_NCV_TO_PJ = 1e-9  # kg × (GJ/tonne) → PJ  (kg→t: 1e-3; GJ→PJ: 1e-6)

# Countries Comtrade cannot cover, with sourced manual fractions consumed by
# build_country_profiles (net importer of everything unless stated).
# XK: no UN M49 code; its power sector burns domestic lignite (KOSTT/ERO annual reports) → coal 0.
# KP: does not report; power coal is domestic anthracite (EIA country analysis) → coal 0.
# RU: has not reported to Comtrade since 2021; net exporter of coal, gas and oil (IEA/EIA) → 0.
# TM: does not report; net gas+oil exporter, world top-5 gas reserves (BP/EI Statistical Review) → 0.
# LY: OPEC net oil exporter; power gas is domestic associated gas, net gas exporter to Italy
#     via Greenstream (EIA country analysis) → 0.
# VE: OPEC net oil exporter; gas is domestic associated production (EIA country analysis) → 0.
# GQ: net crude + LNG exporter (Punta Europa LNG; EIA country analysis) → 0.
# BD: power gas is mostly domestic (Petrobangla); imported LNG ≈ 25% of gas supply
#     (IEA Bangladesh energy profile) → gas 0.25.
MANUAL_OVERRIDES: dict[str, dict[str, float]] = {
    "XK": {"coal": 0.0},
    "KP": {"coal": 0.0},
    "RU": {"coal": 0.0, "gas": 0.0, "oil": 0.0},
    "TM": {"gas": 0.0, "oil": 0.0},
    "LY": {"gas": 0.0, "oil": 0.0},
    "VE": {"gas": 0.0, "oil": 0.0},
    "GQ": {"gas": 0.0, "oil": 0.0},
    "BD": {"gas": 0.25},
}


# Comtrade reporter codes that differ from plain ISO/M49 numeric — the "statistical territory"
# variants (e.g. USA incl. Puerto Rico = 842) and 490 "Other Asia, nes" under which Taiwan's
# trade is recorded. See Comtrade area-code documentation.
REPORTER_CODE_OVERRIDES: dict[str, int] = {
    "US": 842, "FR": 251, "NO": 579, "CH": 757, "IT": 381, "IN": 699, "TW": 490,
}


def m49_by_iso3() -> dict[str, int]:
    with M49_CSV.open(newline="") as f:
        return {row["iso3"]: int(row["m49"]) for row in csv.DictReader(f)}


def _request(params: dict[str, str | int], cache_key: str) -> list[dict[str, Any]]:
    cache = CACHE_DIR / f"{cache_key}.json.gz"
    if cache.exists():
        return list(json.loads(gzip.decompress(cache.read_bytes())))
    rows: list[dict[str, Any]] = []
    for attempt in range(4):
        time.sleep(THROTTLE_S * (attempt + 1))
        try:
            url = f"{PREVIEW_URL}?{urllib.parse.urlencode(params)}"
            with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310
                rows = json.loads(resp.read().decode("utf-8")).get("data") or []
            break
        except Exception:  # noqa: BLE001 — transient rate limit; back off and retry
            if attempt == 3:
                raise
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(gzip.compress(json.dumps(rows).encode("utf-8")))
    return rows


def fetch_totals(m49: int, year: int) -> list[dict[str, Any]]:
    """World-partner totals for every fuel HS code, with breakdown dimensions pinned.

    ``partner2Code=0`` and ``customsCode=C00`` keep the response to at most one row per
    (HS code, flow, mode of transport) — some reporters (e.g. France) otherwise return
    hundreds of breakdown rows and the preview endpoint truncates at 500, silently losing
    flows. Mode of transport stays open because several reporters carry net weight only on
    the per-mode rows (the mot-0 aggregate has value but no weight).
    """
    base: dict[str, str | int] = {
        "reporterCode": m49, "period": year,
        "cmdCode": ",".join(HS_FUELS), "flowCode": "M,X",
        "partnerCode": 0, "partner2Code": 0, "customsCode": "C00",
    }
    rows = _request(base, f"{m49}_{year}_pinned")
    if any(r.get("netWgt") for r in rows):
        return rows
    # Fallback for reporters that use a customs code other than C00: unpinned query, parsed
    # with the max-row heuristic (the aggregate row is ≥ any of its own breakdowns). May be
    # truncated at 500 rows, so it is only a last resort.
    return _request(
        {"reporterCode": m49, "period": year,
         "cmdCode": ",".join(HS_FUELS), "flowCode": "M,X", "partnerCode": 0},
        f"{m49}_{year}_plain",
    )


def net_imports_pj(rows: list[dict[str, Any]]) -> dict[str, float] | None:
    """Net imported energy (PJ, floored at 0 per fuel after netting) from preview rows.

    Algorithm:
        $$E_{fuel} = \\sum_{hs \\in fuel} (W^{M}_{hs} - W^{X}_{hs}) \\cdot NCV_{hs} \\cdot 10^{-9}$$
    ASCII: E[fuel] = sum over HS codes of (import kg - export kg) * NCV[GJ/t] * 1e-9.

    Returns None when no row carries a net weight (country/year not usably reported).
    Per (HS code, flow): prefer the mode-of-transport aggregate row (mot 0); when the reporter
    left its weight empty (value-only aggregates, e.g. France), the per-mode rows partition the
    total, so their weights are summed instead. As a last resort (unpinned fallback responses,
    where the same trade repeats under several breakdown dimensions) the maximum single row is
    used — an aggregate is by construction ≥ any of its own breakdowns.
    """
    mot0: dict[tuple[str, str], float] = {}
    mode_sum: dict[tuple[str, str], float] = {}
    biggest: dict[tuple[str, str], float] = {}
    for rec in rows:
        hs = str(rec.get("cmdCode"))
        flow = str(rec.get("flowCode"))
        if hs not in HS_FUELS or rec.get("partnerCode") != 0 or flow not in ("M", "X"):
            continue
        weight = rec.get("netWgt")
        if not weight:
            continue
        key = (hs, flow)
        pinned = rec.get("partner2Code") in (None, 0) and rec.get("customsCode") in (None, "C00")
        if rec.get("motCode") in (None, 0, "0"):
            if pinned:
                mot0[key] = max(mot0.get(key, 0.0), float(weight))
        elif pinned:
            mode_sum[key] = mode_sum.get(key, 0.0) + float(weight)
        biggest[key] = max(biggest.get(key, 0.0), float(weight))
    if not biggest:
        return None
    net: dict[str, float] = {fuel: 0.0 for fuel in FUELS}
    for key, fallback in biggest.items():
        weight = mot0.get(key) or mode_sum.get(key) or fallback
        fuel, ncv = HS_FUELS[key[0]]
        net[fuel] += (1.0 if key[1] == "M" else -1.0) * weight * ncv * KG_NCV_TO_PJ
    return net


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--countries", nargs="*", help="subset of 2-letter codes (default: all)")
    args = parser.parse_args()

    manifest = json.loads(MANIFEST.read_text())["countries"]
    codes = [c.upper() for c in (args.countries or sorted(manifest))]
    iso3_to_m49 = m49_by_iso3()

    countries: dict[str, Any] = {}
    missing: list[str] = []
    print(f"{'code':<5}{'year':<6}{'coal_PJ':>10}{'gas_PJ':>10}{'oil_PJ':>10}")
    for code in codes:
        m49 = REPORTER_CODE_OVERRIDES.get(code) or iso3_to_m49.get(manifest[code]["iso3"])
        result: dict[str, Any] | None = None
        if m49 is not None:
            for year in YEARS_PREFERRED:
                try:
                    net = net_imports_pj(fetch_totals(m49, year))
                except Exception as exc:  # noqa: BLE001 — report and continue the batch
                    print(f"{code:<5}  ERROR: {exc}", file=sys.stderr)
                    break
                if net is not None:
                    result = {"year": year, "net_imports_pj": {k: round(v, 3) for k, v in net.items()}}
                    break
        if result is None:
            missing.append(code)
            print(f"{code:<5}  no usable Comtrade report — profile build falls back", file=sys.stderr)
            continue
        countries[code] = result
        n = result["net_imports_pj"]
        print(f"{code:<5}{result['year']:<6}{n['coal']:>10.1f}{n['gas']:>10.1f}{n['oil']:>10.1f}")

    out = {
        "source": (
            "UN Comtrade public preview API (annual HS trade, world-partner totals): coal 2701, "
            "oil 2709+2710, natural gas 271111+271121; net weight × IPCC 2006 default NCVs"
        ),
        "url": PREVIEW_URL,
        "years_preferred": list(YEARS_PREFERRED),
        "manual_overrides": MANUAL_OVERRIDES,
        "missing": sorted(missing),
        "countries": countries,
    }
    OUT_PATH.write_text(json.dumps(out, indent=2) + "\n")
    print(f"\nWrote {len(countries)} countries ({len(missing)} missing) → {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
