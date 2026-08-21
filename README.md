# prefsdb

Preferred stock / exchange-traded debt database. Replaces `prefsTEST.xlsx`.

## Why this exists

The spreadsheet worked, but it had accumulated 15 years of drift: 9 different
formulas in the column that computes required yield, two competing penalty
schemes, 106 different formulas for the same distribution amount, and a handful
of rows whose required yield was wired to a *current yield* — a circular
reference that structurally made expensive names look cheap. Roughly 1 row in 7
was not running the formula it appeared to run.

It was also alive only on a desktop running the RealTick RTD add-in, which makes
sharing it impossible.

See `docs/` for the measured findings if you want the detail. The short version:
the errors ran in **both** directions. Formula bugs flattered some names; par
errors hid others. `AIRTP` was carried at par 2.50 against an actual 25, so a
10.6% yielder displayed as 1.06% and could never surface in a screen.

## Design

Three things are kept strictly apart, because they have different owners and
different lifecycles.

| | What | Owner | Refresh |
|---|---|---|---|
| `data/facts.csv` | issue terms from QuantumOnline | derived, never hand-edited | monthly |
| `data/judgment.csv` | your calls: parent, base override, status | **you** | when you say so |
| `data/rates.json`, prices | market data | derived | daily |
| `data/inputs.yaml` | every tunable variable | **you** | when you say so |

The importer refreshes `facts.csv` and **never touches `judgment.csv`**. That
separation is the whole point: a data refresh can't silently overwrite a view you
formed deliberately.

### Harvest and parse are separate steps

`harvest_qol.py` does network and nothing else — it saves each page's text and
parent ticker to `data/raw/qol_pages.jsonl`. `parse_qol.py` is a pure function of
that file.

This matters because QuantumOnline puts most of what we need in an English prose
description rather than labelled fields. Prose parsing takes iteration, and this
split means **refining a regex costs zero HTTP requests**. Harvest once, reparse
as often as you like.

### Standardisation, and why the drift can't come back

- **Store `coupon_rate` + `par` + `freq`. Derive the per-period amount, always.**
  Never store it. Both historical conventions are then unrepresentable.
- **The old `type` column mashed three orthogonal axes together** (hence values
  like `"debt, fix-float"`). Split into `structure` (drives the debt adjustment),
  `coupon_type` (rate behaviour), and independent boolean flags.
- **`cumulative` is a display flag, not an adjustment.** The old workbook's
  `E="cumm"` branch was dead code anyway — nothing ever matched it.
- **Adjustments are a lookup table in `inputs.yaml`, not nested IFs.** Adding a
  type means adding a line, which is how the two competing schemes arose.
- **`reference_rate` is per security.** QOL's LIBOR→SOFR migration is partial and
  inconsistent *within the same issuer* — `ABR-F` and `ADAML` are on SOFR while
  `ACR-C`, `ADAMM` and `ADAMN` still quote 3-mo LIBOR. A global rate cell cannot
  express that. LIBOR names carry the ARRC fallback (Term SOFR + 26.161bp CSA).

### Exclusions are data, not absence

`judgment.csv` gives every security a `status` and an `exclude_reason`
(`NO_PRICE_FEED`, `TRUE_FLOATER`, `NON_US`, `CALLED`, `DELISTED`, `NEEDS_REVIEW`,
`OTHER`). Three reasons this matters: the screener can default to tradeable with a
"show excluded" toggle; the importer never re-proposes a name you deliberately
excluded; and when a reason lapses — a name starts pricing, or you change your
mind on the Canadians — you can review that bucket instead of never revisiting it.

Seeded from the workbook: 577 `TRADEABLE`, 218 `NO_PRICE_FEED`, 11 `TRUE_FLOATER`,
3 `NON_US`.

## Getting started

```bash
pip install -r requirements.txt

python scripts/fetch_rates.py            # SOFR + Treasury curve, no API key needed
python scripts/harvest_qol.py --limit 30 # smoke test: 30 pages
python scripts/parse_qol.py              # parse whatever has been harvested
python scripts/test_parser.py            # score against hand-verified fixtures
```

Then run the **backfill** workflow from the Actions tab for the full 1,151 symbols
(~20 min at the default 1 req/sec). It is resumable, so a timeout is not a
setback.

## Read this before trusting `facts.csv`

**The parser has not been run against live HTML.** It was written in a sandbox
with no network access, so the prose regexes are informed but unverified.

`tests/fixtures/expected.csv` holds hand-verified values for 30 securities chosen
to span every archetype in the universe — baby bonds, TRUPS, fix-to-float on both
LIBOR and SOFR, a true floater, a $1,000-par convertible, a $100-par utility
preferred, a Canadian-dollar issue. After the backfill, run `test_parser.py` and
it prints per-field accuracy.

Expect some fields to need tuning on the first pass. The ones most likely to
need it are `par`, `call_price` and `ratings`, because their prose phrasing
varies most. `parse_qol.py` records every field it could not find in
`parse_flags` rather than emitting a silent null, so gaps are visible in the
output instead of looking like real missing data.

## Still to build

- **`calc.py`** — the required-yield / normal-price engine, reading `inputs.yaml`
- **`build_html.py`** — the screener: parent-grouped like the spreadsheet, filterable
  on any field. The screen is step one; the real work happens off it.
- **Pricing.** Unresolved by design. Three tiers, and they stack:
  1. EOD from a public source on a schedule. The trap is symbol conventions —
     `ACR-C` vs `ACR.PC` vs `ACRpC` — so carry a per-vendor `symbol_map` column.
  2. A local live overlay: a one-column RealTick sheet writing a CSV that the
     screener reads if present, EOD if not. Live on entitled desktops, nothing
     over the wire.
  3. An Eze/RealTick API. **Ask about redistribution rights before building on
     it** — exchange data agreements generally forbid pushing quotes to a hosted
     page, private link or not. That is the real reason tier 2 exists.
- **Market cap** for the 62 parent blocks where RealTick returned nothing. 25 of
  those still have live preferreds, which means the *parent symbol* is stale, not
  the security dead. QOL's parent ticker fixes them — see
  `data/parents.csv`, column `symbol_suspect`.

## Layout

```
data/
  universe.txt      1,151 symbols: the QOL table plus workbook-only names
  judgment.csv      your calls. hand-maintained. never overwritten by the importer.
  parents.csv       parent tickers, issue counts, stale-symbol suspects
  inputs.yaml       every tunable variable
  facts.csv         generated by parse_qol.py
  rates.json        generated by fetch_rates.py
  raw/              the harvested corpus. reparse from here; never refetch.
scripts/
  harvest_qol.py    network only, resumable, polite
  parse_qol.py      pure function of raw/ -> facts.csv
  test_parser.py    scores the parser against fixtures
  fetch_rates.py    SOFR (NY Fed API) + Treasury curve
tests/fixtures/
  expected.csv      hand-verified, spans every archetype
```
