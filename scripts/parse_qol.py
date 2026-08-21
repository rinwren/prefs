#!/usr/bin/env python3
"""Parse the harvested QuantumOnline corpus into data/facts.csv.

Pure function of data/raw/qol_pages.jsonl -- no network. Re-run freely.

HOW A QOL DETAIL PAGE IS BUILT (this drives everything below)

Every page carries the site's full navigation before the security content, and
those menus contain phrases that look exactly like the facts we want:
"Mandatory Convertible Securities", "Securities Called for Redemption",
"Preferreds eligible for the 15% Tax Rate", "Glossary of Income Investing Terms".
Matching anywhere on the page therefore produces confident nonsense. So the
first job is to cut the page down to the security block, and every regex runs
only inside that block.

Within the block there are three zones, in decreasing order of trustworthiness:

  1. The SUMMARY TABLE -- a fixed 14-value sequence (exchange, "Chart",
     coupon rate/type, annual amount, liquidation preference, call price, call
     date, maturity, ratings, as-of date, distribution dates, two ExDiv links,
     15% tax rate). Positional and unambiguous. Note the last value: QOL states
     QDI eligibility as a plain Yes/No, so it does not need inferring.
  2. LABELLED LINES -- ticker, CUSIP, exchange, security type, previous ticker.
  3. The PROSE DESCRIPTION -- the only source for cumulative-vs-not, the
     fix-to-float reset terms, and redemption status.

Prefer the table, fall back to prose, and record anything not found in
parse_flags rather than emitting a silent null.
"""
from __future__ import annotations
import csv, json, re, sys
from pathlib import Path

SRC = Path("data/raw/qol_pages.jsonl")
DST = Path("data/facts.csv")

FIELDS = ["symbol", "found", "security_name", "security_type", "structure", "parent_ticker",
          "cusip", "exchange", "currency", "cumulative", "qdi_eligible", "coupon_type",
          "coupon_pct", "par", "annual_amount", "call_date", "call_price", "maturity_date",
          "reference_rate", "float_spread_bps", "float_floor_pct", "reset_date", "ratings",
          "distribution_dates", "qol_as_of", "previous_ticker", "previous_ticker_changed",
          "redeemed", "qol_table_raw", "parse_flags", "fetched_at"]

# --- zone extraction ------------------------------------------------------
# The security block starts at the LABELLED ticker line ("Ticker Symbol:" with a
# colon), never at the bare "Ticker Symbol" of the search form above it.
TICKER_LINE = re.compile(r"^Ticker Symbol:\s*(\S+)", re.M)
BLOCK_END = re.compile(r"Company'?s Online Information Links|HOME PAGE:|"
                       r"Find a problem\?|Copyright &copy;|Copyright ©", re.I)
DESC_RE = re.compile(r"QUANTUMONLINE\.COM SECURITY DESCRIPTION:\s*(.*?)(?=\n(?:Stock|Exchange)\s*\n)",
                     re.S | re.I)
DESC_FALLBACK = re.compile(r"QUANTUMONLINE\.COM SECURITY DESCRIPTION:\s*(.{50,4000})", re.S | re.I)
TABLE_RE = re.compile(r"\n15%\s*\n\s*Tax Rate\s*\n(.*)", re.S)
# The table ends where the page moves on. Without this, the ratings cell absorbs
# the parent-company link, the IPO line and any NOTE that follows.
TABLE_END = re.compile(r"^\s*(Go to Parent Company|IPO\s*-|Link to IPO|"
                       r"Previous Ticker Symbol:|Market Value|NOTE:|Find All Related)",
                       re.I | re.M)

STRUCTURE = [(r"exchange[- ]traded debt", "BABY_BOND"),
             (r"third party trust preferred", "TP"),
             (r"trust preferred", "TRUPS"),
             (r"\bunit", "UNIT"),
             (r"preferred|preference", "PREFERRED")]
CPN_TYPE_TABLE = {"fixfloat": "FIX_TO_FLOAT", "fixed/adj": "RESET", "reset rate": "RESET",
                  "variable": "VARIABLE", "n.a.": ""}
REF_RATE = [(r"(?:three[- ]month|3[- ]month|3-mo)\s+term\s+sofr", "3M_TERM_SOFR"),
            (r"\bterm\s+sofr\b", "3M_TERM_SOFR"),
            (r"(?:three[- ]month|3[- ]month|3-mo)\s+sofr", "3M_SOFR"),
            (r"\bsofr\b", "SOFR"),
            (r"(?:three[- ]month|3[- ]month|3-mo)\s+libor", "3M_LIBOR"),
            (r"\blibor\b", "LIBOR"),
            (r"government of canada", "GOC"),
            (r"(\d+)[- ]year\s+(?:u\.?s\.?\s+)?treasury|\bcmt\b", "CMT")]


def security_block(text: str) -> tuple[str, str, str, list[str]]:
    """Return (block, description, table_tail, warnings)."""
    warn = []
    m = TICKER_LINE.search(text)
    if not m:
        # some pages put it on one wrapped line
        m = re.search(r"Ticker Symbol:\s*(\S+)", text)
    if not m:
        return "", "", "", ["no_ticker_label"]
    start = text.rfind("\n", 0, m.start())          # keep the name line above it
    start = 0 if start < 0 else text.rfind("\n", 0, start) + 1
    e = BLOCK_END.search(text, m.end())
    block = text[start: e.start() if e else None]
    dm = DESC_RE.search(block) or DESC_FALLBACK.search(block)
    desc = (dm.group(1) if dm else "").strip()
    if not dm:
        warn.append("no_description")
    tm = TABLE_RE.search(block)
    tail = tm.group(1) if tm else ""
    if tail:
        te = TABLE_END.search(tail)
        if te:
            tail = tail[: te.start()]
    return block, desc, tail, warn


NAV_JUNK = re.compile(r"^(HOME|MARKETS|NEWS|LOGIN|INCOME|STOCK|SPECIAL|INFORMATION|"
                      r"SERVICES|Hint:|by |Track all|AAPL stock|Session\.|"
                      r"Glossary|Explanations of|Preferreds eligible|Securities |"
                      r"Mandatory Convertible|Traditional |Trust Preferred|Third Party|"
                      r"All Exchange|All Preferred|Municipal Bond|Convertible Debt|"
                      r"QuantumOnline|Become a|List Descriptions|Table Descriptions|"
                      r"IPOs? of|Banks List|Bank Holding|Real Estate Investment|"
                      r"Closed-end|Exchange-Traded Funds|Master Limited|Royalty Trusts|"
                      r"Business Development|Income Deposit|Special (?:Investment|Purpose)|"
                      r"SIP-|U\.S\.|European|Asian|Cyrpto|Foreign Currency|Company |"
                      r"Dividend Reports|Earnings Release|Initial Public|Major Shareholder|"
                      r"Mergers and|Stock Market|Forbes|Email Alert|Find a problem|"
                      r"Have you filled|Copyright|FYI, |Some users|Yahoo\.com|The Square|"
                      r"Square\.com|Users can|We have|There (?:is|are)|The QuantumOnline|"
                      r"We apologize|Tips on|What Income|Income Investing|Privacy Policy|"
                      r"About QOL|QOL SUPPORT|CONTACT|GUESTBOOK|USING QOL|LINKS|ABOUT)", re.I)


def security_name(text: str) -> str:
    """The security's full name is the last real line ABOVE the ticker line.

    It is not necessarily the immediately preceding line -- QOL emits several
    blank rows between them -- so scan back rather than anchoring on \n.
    The name matters beyond cosmetics: it carries the exact coupon (the summary
    table rounds 6.375% to 6.38) and it is the only place "Convertible" appears
    reliably.
    """
    m = re.search(r"^Ticker Symbol:", text, re.M) or re.search(r"Ticker Symbol:", text)
    if not m:
        return ""
    seen = 0
    for line in reversed(text[: m.start()].split("\n")):
        t = line.strip()
        if len(t) < 8 or t == "-->":
            continue                       # blank rows and stray comment closers
        seen += 1
        if seen > 30:
            break                          # do not wander up into the menus
        if NAV_JUNK.match(t) or t.endswith(":"):
            continue
        return t
    return ""


DATEISH = re.compile(r"^(\d{1,2}/\d{1,2}/\d{2,4}|None|n\.a\.|NONE)$", re.I)
MONEYISH = re.compile(r"^\$?\s*[\d,]+(?:\.\d+)?$")
DIST_SHAPE = re.compile(r"\d{1,2}/\d{1,2}\s*[,&]|Last business day|First Day|"
                        r"Monthly|Quarterly|Semi-?annual", re.I)


def table_values(tail: str) -> list[str]:
    """Summary-table values, boilerplate removed.

    "Chart" is present only when the security still charts, so a delisted name
    has one fewer value -- which is why this is parsed by SHAPE below rather
    than by position.
    """
    vals = [ln.strip() for ln in tail.split("\n") if ln.strip()]
    return [v for v in vals
            if not v.startswith("Click for") and v not in ("Chart", "Stock", "Exchange")]


def parse_table(vals: list[str]) -> dict:
    """Pull the summary table by value shape, not index.

    Two things break positional parsing, and both are common:
      - "Chart" is absent for delisted securities
      - the Moody's/S&P cell renders as one line ("NR NR") or two ("NR", "BBB")
    So: take the Yes/No tax rate off the end, the exchange off the front, lift
    the distribution schedule out by its shape, then read the remainder as
    coupon -> three amounts -> two dates -> ratings -> as-of date.
    """
    v = list(vals)
    out = {}
    for i in range(len(v) - 1, -1, -1):
        if re.match(r"^(Yes|No)\b", v[i], re.I):
            out["tax"] = v[i]
            del v[i]
            break
    if v and re.fullmatch(r"[A-Za-z.]{2,10}", v[0]):
        out["exchange"] = v.pop(0)
    for i, x in enumerate(v):
        if DIST_SHAPE.search(x):
            out["dist"] = x
            del v[i]
            break
    if v:
        out["cpn"] = v.pop(0)
    for key in ("ann", "liq", "callpx"):
        if v and MONEYISH.match(v[0]):
            out[key] = v.pop(0)
    for key in ("calldt", "matdt"):
        if v and DATEISH.match(v[0]):
            out[key] = v.pop(0)
    if v and DATEISH.match(v[-1]):
        out["asof"] = v.pop()
    if v:
        out["ratings"] = " ".join(v)
    return out


def rx(pat, text, group=1, flags=re.I):
    m = re.search(pat, text, flags)
    if not m:
        return None
    try:
        return (m.group(group) or "").strip() or None
    except IndexError:
        return None


def num(s):
    if s is None:
        return None
    s = re.sub(r"[^\d.\-]", "", str(s))
    try:
        return float(s)
    except ValueError:
        return None


def pick(table, text, default=None):
    for pat, val in table:
        if re.search(pat, text, re.I):
            return val
    return default


def parse_one(rec: dict) -> dict:
    out = {k: "" for k in FIELDS}
    out["symbol"] = rec["symbol"]
    out["found"] = rec.get("found")
    out["parent_ticker"] = rec.get("parent_ticker") or ""
    out["fetched_at"] = rec.get("fetched_at", "")
    text = rec.get("text") or ""
    if not rec.get("found") or not text:
        out["parse_flags"] = "PAGE_NOT_FOUND"
        return out

    block, desc, tail, flags = security_block(text)
    if not block:
        out["parse_flags"] = ";".join(flags) or "NO_BLOCK"
        return out
    tv = table_values(tail)

    out["security_name"] = security_name(text)
    out["cusip"] = rx(r"CUSIP:\s*([A-Z0-9]{6,12})", block) or ""
    out["exchange"] = rx(r"Exchange:\s*([A-Za-z.]{2,10})", block) or (tv[0] if tv else "")
    out["security_type"] = rx(r"Security Type:\s*\n?\s*([^\n]{3,80})", block) or ""
    out["structure"] = pick(STRUCTURE, out["security_type"] or out["security_name"], "PREFERRED")
    out["previous_ticker"] = rx(r"Previous Ticker Symbol:\s*([A-Z0-9.\-]{1,10})", block) or ""
    out["previous_ticker_changed"] = rx(r"Previous Ticker Symbol:[^\n]*?Changed:\s*([\d/]{6,10})", block) or ""

    # --- summary table (by shape, see parse_table) ------------------------
    out["qol_table_raw"] = " | ".join(tv)
    t = parse_table(tv)
    if not t:
        flags.append("summary_table")
    out["exchange"] = out["exchange"] or t.get("exchange", "")
    out["annual_amount"] = num(t.get("ann")) or ""
    out["par"] = num(t.get("liq")) or ""
    out["call_price"] = num(t.get("callpx")) or ""
    cd, md = t.get("calldt", ""), t.get("matdt", "")
    out["call_date"] = "" if cd.lower() in ("none", "n.a.", "") else cd
    out["maturity_date"] = "" if md.lower() in ("none", "n.a.", "") else md
    out["ratings"] = t.get("ratings", "")
    out["qol_as_of"] = t.get("asof", "")
    out["distribution_dates"] = t.get("dist", "")
    tax = t.get("tax", "")
    if tax.lower().startswith("yes"):
        out["qdi_eligible"] = True
    elif tax.lower().startswith("no"):
        out["qdi_eligible"] = False
    else:
        flags.append("qdi_eligible")
    cpn_raw = t.get("cpn", "")
    ct = CPN_TYPE_TABLE.get(cpn_raw.strip().lower())
    if ct:
        out["coupon_type"] = ct

    # --- prose-only facts ------------------------------------------------
    if re.search(r"non-?cumulative", desc, re.I):
        out["cumulative"] = False
    elif re.search(r"\bcumulative\b", desc, re.I):
        out["cumulative"] = True
    elif out["structure"] in ("BABY_BOND",):
        out["cumulative"] = ""          # meaningless for debt; not a gap
    else:
        flags.append("cumulative")

    # Coupon, most precise source first. The summary table ROUNDS (6.375 -> 6.38),
    # so it is the last resort, never the first.
    c = (rx(r"(\d{1,2}\.\d{1,4})\s*%", out["security_name"] or "")
         or rx(r"(\d{1,2})\s*%", out["security_name"] or "")
         or rx(r"(?:distributions|interest|dividends) of\s+(\d{1,2}\.?\d{0,4})\s*%", desc))
    out["coupon_pct"] = num(c) or ""
    if out["coupon_pct"] == "" and out["annual_amount"] != "" and out["par"] not in ("", 0):
        out["coupon_pct"] = round(out["annual_amount"] / out["par"] * 100, 4)
    if out["coupon_pct"] == "" and num(cpn_raw) is not None:
        out["coupon_pct"] = num(cpn_raw)
    if out["coupon_pct"] == "":
        flags.append("coupon_pct")
    if out["par"] == "":
        p = (rx(r"liquidation preference (?:of\s+)?\$?\s*([\d,]+(?:\.\d+)?)", desc)
             or rx(r"(?:principal amount|face value) (?:of\s+)?\$?\s*([\d,]+(?:\.\d+)?)", desc)
             or rx(r"\$\s*([\d,]+(?:\.\d+)?)\s+per (?:share|note|bond)", desc))
        out["par"] = num(p) or ""
        if out["par"] == "":
            flags.append("par")
    if not out["call_date"]:
        out["call_date"] = (rx(r"redeemable[^.]{0,160}?on or after\s+([\d/]{6,10})", desc)
                            or ("any time" if re.search(r"redeemable[^.]{0,80}at any time", desc, re.I) else ""))

    # Gate on the table's coupon type, or an explicit float sentence. NOT on the
    # bare word "floating": every fixed-rate description mentions a floating rate
    # in its change-of-control language, and "will be paid quarterly" also matched
    # an earlier, sloppier pattern.
    floats = re.search(r"at a floating rate|floating rate (?:of|equal to)|"
                       r"rate will (?:be reset|float)|will be paid at a floating", desc, re.I)
    if out["coupon_type"] in ("FIX_TO_FLOAT", "FLOATER", "RESET", "VARIABLE") or floats:
        if not out["coupon_type"]:
            out["coupon_type"] = "FIX_TO_FLOAT"
        out["reference_rate"] = pick(REF_RATE, desc, "") or ""
        sp = (rx(r"(?:plus|spread of)\s+(?:a spread of\s+)?(\d{1,2}\.\d{1,4})\s*%", desc)
              or rx(r"plus a spread of\s+(\d{1,2}\.\d{1,4})\s*%", desc))
        out["float_spread_bps"] = round(num(sp) * 100, 1) if num(sp) is not None else ""
        fl = rx(r"(?:lower than|not be less than|floor of|minimum of)\s+(\d{1,2}\.\d{1,4})\s*%", desc)
        out["float_floor_pct"] = num(fl) or ""
        out["reset_date"] = rx(r"From\s+([\d/]{6,10})", desc) or ""
        for f in ("reference_rate", "float_spread_bps"):
            if out[f] == "":
                flags.append(f)
    elif not out["coupon_type"]:
        out["coupon_type"] = "FIXED"
    if re.search(r"convertible", out["security_name"] or "", re.I) and \
       out["coupon_type"] == "FIXED":
        out["coupon_type"] = ("MAND_CONVERT"
                             if re.search(r"mandator", out["security_name"], re.I)
                             else "CONVERTIBLE")

    out["currency"] = ("CAD" if re.search(r"canadian dollar|government of canada|"
                                          r"\bC\$", desc, re.I) else "USD")
    # The delisting/redemption sentence often sits in a NOTE outside the
    # description paragraph, so scan the whole block. Safe: the block excludes nav.
    out["redeemed"] = bool(re.search(r"has been (?:redeemed|called for redemption)|"
                                     r"been fully redeemed|no longer trading|"
                                     r"has been delisted|called for redemption",
                                     block, re.I))
    out["parse_flags"] = ";".join(dict.fromkeys(flags))
    return out


def main() -> int:
    if not SRC.exists():
        print(f"missing {SRC} -- run scripts/harvest_qol.py first", file=sys.stderr)
        return 1
    latest = {}
    with SRC.open() as f:
        for line in f:
            line = line.strip()
            if line:
                r = json.loads(line)
                latest[r["symbol"]] = r
    parsed = [parse_one(r) for r in sorted(latest.values(), key=lambda z: z["symbol"])]
    DST.parent.mkdir(parents=True, exist_ok=True)
    with DST.open("w", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(parsed)

    n = len(parsed)
    nf = sum(1 for p in parsed if p["parse_flags"] == "PAGE_NOT_FOUND")
    print(f"parsed {n} symbols -> {DST}")
    print(f"  page not found        : {nf}")
    print(f"  parent ticker resolved: {sum(1 for p in parsed if p['parent_ticker'])}/{n}")
    print(f"  qdi from table        : {sum(1 for p in parsed if p['qdi_eligible'] != '')}/{n}")
    print(f"  par resolved          : {sum(1 for p in parsed if p['par'] != '')}/{n}")
    tally = {}
    for p in parsed:
        for fl in (p["parse_flags"] or "").split(";"):
            if fl and fl != "PAGE_NOT_FOUND":
                tally[fl] = tally.get(fl, 0) + 1
    if tally:
        print("  parse gaps:")
        for k, v in sorted(tally.items(), key=lambda z: -z[1]):
            print(f"    {v:5d}  {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
