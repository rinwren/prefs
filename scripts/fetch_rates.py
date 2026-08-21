#!/usr/bin/env python3
"""Pull SOFR and the Treasury curve into data/rates.json.

SOFR comes from the New York Fed markets API -- no key, no registration:
  https://markets.newyorkfed.org/api/rates/secured/sofr/last/1.json
Verified 2026-08-21: SOFR 3.63%, effective 2026-08-20.

LIBOR is gone. Nothing here publishes it, and the old workbook's 0.75% cell was
years stale. Issues whose documents still reference LIBOR carry the ARRC fallback
instead -- see the libor_fallback block below.
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path
from urllib.request import Request, urlopen

OUT = Path("data/rates.json")
UA = "prefsdb/1.0 (research; contact rin@coveyequity.com)"
SOFR = "https://markets.newyorkfed.org/api/rates/secured/sofr/last/1.json"
TREASURY = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
            "daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve&"
            "field_tdr_date_value={year}&page&_format=csv")


def get(url: str, timeout: int = 30) -> str:
    return urlopen(Request(url, headers={"User-Agent": UA}), timeout=timeout
                   ).read().decode("utf-8", "replace")


def sofr() -> dict:
    d = json.loads(get(SOFR))
    row = (d.get("refRates") or d.get("rates") or [{}])[0]
    return {"rate": float(row["percentRate"]) / 100.0,
            "effective_date": row.get("effectiveDate", ""),
            "source": SOFR}


def treasury(year: int) -> dict:
    """Most recent row of the daily yield curve. Column names carry the tenor."""
    import csv, io
    text = get(TREASURY.format(year=year))
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise RuntimeError("treasury csv was empty")
    latest = rows[0]                       # Treasury serves newest-first
    tenors = {}
    for k, v in latest.items():
        if k == "Date" or not v:
            continue
        try:
            tenors[k.strip()] = float(v) / 100.0
        except ValueError:
            pass
    return {"date": latest.get("Date", ""), "tenors": tenors,
            "source": TREASURY.format(year=year)}


def main() -> int:
    out = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "libor_fallback": {
               "note": ("3-mo LIBOR ceased publication. Under the ARRC/LIBOR Act "
                        "fallback, 3-mo LIBOR is replaced by 3-mo CME Term SOFR plus "
                        "a fixed credit spread adjustment of 26.161bp. Issues whose "
                        "reference_rate parses as 3M_LIBOR should be evaluated as "
                        "3M_TERM_SOFR + spread + csa_bps."),
               "csa_bps": 26.161}}
    errs = []
    try:
        out["sofr"] = sofr()
    except Exception as e:
        errs.append(f"sofr: {type(e).__name__}: {e}")
    try:
        out["treasury"] = treasury(time.gmtime().tm_year)
    except Exception as e:
        errs.append(f"treasury: {type(e).__name__}: {e}")
    if errs:
        out["errors"] = errs
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({k: v for k, v in out.items() if k != "libor_fallback"}, indent=2))
    for e in errs:
        print("WARN " + e, file=sys.stderr)
    return 1 if "sofr" not in out else 0


if __name__ == "__main__":
    sys.exit(main())
