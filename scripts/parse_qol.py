#!/usr/bin/env python3
"""Parse the harvested QuantumOnline corpus into data/facts.csv.

Pure function of data/raw/qol_pages.jsonl -- no network. Re-run it as often as
you like; refining a regex costs nothing.

QOL puts a handful of fields in labelled form (ticker, CUSIP, exchange, security
type, previous ticker) and everything else in an English prose description. The
labelled fields parse deterministically. The prose fields are matched with
several alternative patterns each, and anything that does not match is recorded
in parse_flags rather than silently emitted as null -- so a coverage gap is
visible instead of looking like real missing data.

Run scripts/test_parser.py to score the parser against tests/fixtures/expected.csv,
which holds hand-verified values for a sample spanning every security archetype.
"""
from __future__ import annotations
import csv, json, re, sys
from pathlib import Path

SRC = Path("data/raw/qol_pages.jsonl")
DST = Path("data/facts.csv")

FIELDS = ["symbol", "found", "security_type", "structure", "parent_ticker", "issuer_name",
          "cusip", "exchange", "currency", "cumulative", "qdi_eligible", "coupon_type",
          "coupon_pct", "par", "call_date", "call_price", "maturity_date",
          "reference_rate", "float_spread_bps", "float_floor_pct", "ratings",
          "distribution_dates", "previous_ticker", "previous_ticker_changed",
          "redeemed", "qol_note", "parse_flags", "fetched_at"]

STRUCTURE = [
    (r"exchange[- ]traded (?:debt|note)", "BABY_BOND"),
    (r"\bsenior notes?\b|\bsubordinated notes?\b|\bbaby bond\b", "BABY_BOND"),
    (r"trust preferred|\btrups\b", "TRUPS"),
    (r"third party trust preferred|\btp\b", "TP"),
    (r"\bunits?\b", "UNIT"),
    (r"preferred|preference", "PREFERRED"),
]
COUPON_TYPE = [
    (r"mandatory\w* convertible", "MAND_CONVERT"),
    (r"convertible", "CONVERTIBLE"),
    (r"fixed[-/ ]to[-/ ]float|fixed/float|fix(?:ed)?[-/ ]?float", "FIX_TO_FLOAT"),
    (r"reset rate|fixed[-/ ]rate reset|\breset\b", "RESET"),
    (r"floating rate|\bfloater\b", "FLOATER"),
    (r"variable rate|\bvariable\b", "VARIABLE"),
]
REF_RATE = [
    (r"(?:three[- ]month|3[- ]?mo(?:nth)?|3M)\s+(?:term\s+)?sofr", "3M_TERM_SOFR"),
    (r"\bsofr\b", "SOFR"),
    (r"(?:three[- ]month|3[- ]?mo(?:nth)?|3M)\s+libor", "3M_LIBOR"),
    (r"\blibor\b", "LIBOR"),
    (r"(\d+)[- ]year\s+(?:u\.?s\.?\s+)?treasury|(\d+)yr\s+cmt", "CMT"),
    (r"government of canada", "GOC"),
]


def rx(pattern, text, group=1, flags=re.I):
    m = re.search(pattern, text, flags)
    if not m:
        return None
    try:
        return (m.group(group) or "").strip() or None
    except IndexError:
        return None


def num(s):
    if s is None:
        return None
    s = str(s).replace("$", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def first_match(table, text, default=None):
    for pat, val in table:
        if re.search(pat, text, re.I):
            return val
    return default


def parse_one(rec: dict) -> dict:
    t = rec.get("text") or ""
    out = {k: "" for k in FIELDS}
    out["symbol"] = rec["symbol"]
    out["found"] = rec.get("found")
    out["parent_ticker"] = rec.get("parent_ticker") or ""
    out["fetched_at"] = rec.get("fetched_at", "")
    flags = []
    if not rec.get("found"):
        out["parse_flags"] = "PAGE_NOT_FOUND"
        return out

    out["cusip"] = rx(r"CUSIP:\s*([A-Z0-9]{6,12})", t) or ""
    out["exchange"] = rx(r"Exchange:\s*([A-Za-z .]{2,12})", t) or ""
    out["security_type"] = rx(r"Security Type:\s*([^\n]{3,80})", t) or ""
    out["previous_ticker"] = rx(r"Previous Ticker Symbol:\s*([A-Z0-9.\-]{1,10})", t) or ""
    out["previous_ticker_changed"] = rx(r"Changed:?\s*([\d/]{6,10})", t) or ""
    out["issuer_name"] = rx(r"^([^\n]{5,120})\nTicker Symbol", t, flags=re.I | re.M) or ""

    blob = (out["security_type"] + " " + t)
    out["structure"] = first_match(STRUCTURE, out["security_type"] or blob, "PREFERRED")
    out["coupon_type"] = first_match(COUPON_TYPE, blob, "FIXED")

    # cumulative: the negative form must win, so test it first
    if re.search(r"non-?cumulative", blob, re.I):
        out["cumulative"] = False
    elif re.search(r"\bcumulative\b", blob, re.I):
        out["cumulative"] = True
    else:
        flags.append("cumulative")

    # QDI: QOL states eligibility, often with the reason
    if re.search(r"not eligible for the (?:preferential )?15%", t, re.I) or \
       re.search(r"NOT eligible", t):
        out["qdi_eligible"] = False
    elif re.search(r"eligible for the (?:preferential )?15%", t, re.I):
        out["qdi_eligible"] = True
    elif out["structure"] in ("BABY_BOND", "TRUPS", "TP"):
        out["qdi_eligible"] = False        # interest, not a dividend
        flags.append("qdi_inferred_from_structure")
    else:
        flags.append("qdi_eligible")

    cpn = (rx(r"(\d{1,2}\.\d{1,4})\s*%", out["issuer_name"] or "")
           or rx(r"(?:coupon|dividend|distribution|annual)\s+rate[^%\d]{0,30}(\d{1,2}\.\d{1,4})\s*%", t)
           or rx(r"(\d{1,2}\.\d{1,4})\s*%", t))
    out["coupon_pct"] = num(cpn) or ""
    if out["coupon_pct"] == "":
        flags.append("coupon_pct")

    par = (rx(r"liquidation (?:preference|value)[^$\d]{0,40}\$?\s*([\d,]+(?:\.\d+)?)", t)
           or rx(r"\$?\s*([\d,]+(?:\.\d+)?)\s*(?:per share|liquidation)", t))
    out["par"] = num(par) or ""
    if out["par"] == "":
        flags.append("par")

    out["call_date"] = (rx(r"redeemable[^.]{0,120}?on or after\s+([\d/]{6,10})", t)
                        or rx(r"call(?:able)? date[:\s]{1,4}([\d/]{6,10})", t) or "")
    if not out["call_date"] and re.search(r"redeemable[^.]{0,60}at any time", t, re.I):
        out["call_date"] = "any time"
    if not out["call_date"]:
        flags.append("call_date")
    out["call_price"] = num(rx(r"(?:redeem\w*|call\w*)[^.]{0,120}?at\s+\$?\s*([\d,]+(?:\.\d+)?)", t)) or ""
    out["maturity_date"] = (rx(r"matur\w*[^.]{0,60}?on\s+([\d/]{6,10})", t)
                            or rx(r"matur\w+ date[:\s]{1,4}([\d/]{6,10})", t) or "")

    if out["coupon_type"] in ("FIX_TO_FLOAT", "FLOATER", "RESET", "VARIABLE"):
        out["reference_rate"] = first_match(REF_RATE, t, "") or ""
        sp = (rx(r"plus\s+(\d{1,2}\.\d{1,4})\s*%", t)
              or rx(r"spread of\s+(\d{1,2}\.\d{1,4})\s*%", t)
              or rx(r"\+\s*(\d{1,2}\.\d{1,4})\s*%", t))
        out["float_spread_bps"] = round(num(sp) * 100, 1) if num(sp) is not None else ""
        fl = rx(r"(?:floor|not less than|minimum)[^%\d]{0,30}(\d{1,2}\.\d{1,4})\s*%", t)
        out["float_floor_pct"] = num(fl) or ""
        for f in ("reference_rate", "float_spread_bps"):
            if out[f] == "":
                flags.append(f)

    out["ratings"] = rx(r"(?:Moody'?s?|Ratings?)[:\s/]{1,4}([A-Za-z0-9+\-/ ]{2,24})", t) or ""
    out["distribution_dates"] = rx(r"(?:pay(?:able|ment)|distribution) dates?[:\s]{1,4}([^\n]{4,60})", t) or ""
    out["currency"] = "CAD" if re.search(r"\bcanadian dollar|\bCAD\b|government of canada", t, re.I) else "USD"
    out["redeemed"] = bool(re.search(r"has been (?:redeemed|called)|no longer (?:traded|outstanding)|been fully redeemed", t, re.I))
    out["qol_note"] = (rx(r"NOTE:\s*([^\n]{5,200})", t) or "")
    out["parse_flags"] = ";".join(flags)
    return out


def main() -> int:
    if not SRC.exists():
        print(f"missing {SRC} -- run scripts/harvest_qol.py first", file=sys.stderr)
        return 1
    rows, seen = [], set()
    with SRC.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            seen.add(rec["symbol"])          # last record for a symbol wins
            rows.append(rec)
    latest = {}
    for rec in rows:
        latest[rec["symbol"]] = rec
    parsed = [parse_one(r) for r in sorted(latest.values(), key=lambda z: z["symbol"])]
    DST.parent.mkdir(parents=True, exist_ok=True)
    with DST.open("w", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(parsed)

    n = len(parsed)
    notfound = sum(1 for p in parsed if p["parse_flags"] == "PAGE_NOT_FOUND")
    flagged = sum(1 for p in parsed if p["parse_flags"] and p["parse_flags"] != "PAGE_NOT_FOUND")
    print(f"parsed {n} symbols -> {DST}")
    print(f"  page not found : {notfound}")
    print(f"  with parse gaps: {flagged}")
    tally = {}
    for p in parsed:
        for fl in (p["parse_flags"] or "").split(";"):
            if fl and fl != "PAGE_NOT_FOUND":
                tally[fl] = tally.get(fl, 0) + 1
    for k, v in sorted(tally.items(), key=lambda z: -z[1]):
        print(f"    {v:5d}  {k}")
    print(f"  parent ticker found: {sum(1 for p in parsed if p['parent_ticker'])}/{n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
