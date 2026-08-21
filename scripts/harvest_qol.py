#!/usr/bin/env python3
"""Harvest QuantumOnline security detail pages.

Deliberately split from parsing. This script only does NETWORK: it fetches each
detail page, pulls out the parent ticker (which lives in a link, not the prose)
and the page's text, and appends one JSON line per symbol to data/raw/qol_pages.jsonl.

Parsing that corpus is parse_qol.py's job. Keeping them apart means a regex fix
never costs another 1,151 HTTP requests -- reparse is free and offline.

Resumable: symbols already in the jsonl are skipped, so an interrupted run just
picks up. Polite by default (1 req/sec, single-threaded, real UA, one retry).
"""
from __future__ import annotations
import argparse, json, os, random, re, sys, time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

BASE = "https://www.quantumonline.com/search.cfm"
UA = "prefsdb/1.0 (research; contact rin@coveyequity.com)"
OUT = Path("data/raw/qol_pages.jsonl")
PARENT_RE = re.compile(r"Parent Company'?s? Record\s*\(([^)]+)\)", re.I)
TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
BR_RE = re.compile(r"<(br|/tr|/p|/div|/table)[^>]*>", re.I)


def page_text(html: str) -> str:
    h = TAG_RE.sub(" ", html)
    h = BR_RE.sub("\n", h)
    h = re.sub(r"<[^>]+>", " ", h)
    h = (h.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
          .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'"))
    h = re.sub(r"[ \t\xa0]+", " ", h)
    h = re.sub(r" ?\n ?", "\n", h)
    h = re.sub(r"\n{2,}", "\n", h)
    return h.strip()


def slice_detail(text: str) -> str:
    i = text.find("Ticker Symbol")
    j = text.find("Web page design latest update")
    if i < 0:
        return text
    return text[i:j if j > i else None].strip()


def fetch(symbol: str, timeout: int) -> tuple[int, str]:
    url = BASE + "?" + urlencode({"tickersymbol": symbol, "sopt": "symbol"})
    req = Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    with urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="data/universe.txt",
                    help="newline-delimited symbol list")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--limit", type=int, default=0, help="stop after N new fetches (0 = all)")
    a = ap.parse_args()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    if OUT.exists():
        with OUT.open() as f:
            for line in f:
                try:
                    done.add(json.loads(line)["symbol"])
                except Exception:
                    pass

    wanted = [s.strip() for s in Path(a.symbols).read_text().split("\n") if s.strip()]
    todo = [s for s in wanted if s not in done]
    print(f"universe {len(wanted)} | already harvested {len(done)} | to fetch {len(todo)}",
          flush=True)
    if a.limit:
        todo = todo[: a.limit]

    ok = miss = err = 0
    with OUT.open("a") as out:
        for i, sym in enumerate(todo, 1):
            status, html, error = 0, "", ""
            for attempt in (1, 2):
                try:
                    status, html = fetch(sym, a.timeout)
                    error = ""
                    break
                except HTTPError as e:
                    status, error = e.code, f"HTTP {e.code}"
                except (URLError, TimeoutError, OSError) as e:
                    error = f"{type(e).__name__}: {e}"
                if attempt == 1:
                    time.sleep(a.delay * 3)
            text = slice_detail(page_text(html)) if html else ""
            pm = PARENT_RE.search(html) if html else None
            # QOL serves a "no such security" page with a 200, so detect by content
            found = bool(text) and "Ticker Symbol" in text
            rec = {"symbol": sym, "http_status": status, "error": error,
                   "parent_ticker": pm.group(1).strip() if pm else "",
                   "found": found, "html_len": len(html), "text": text,
                   "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            if error:
                err += 1
            elif found:
                ok += 1
            else:
                miss += 1
            if i % 25 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)}  ok={ok} notfound={miss} error={err}", flush=True)
            time.sleep(a.delay + random.uniform(0, 0.25))

    print(f"done. ok={ok} notfound={miss} error={err} -> {OUT}")
    return 1 if err and not ok else 0


if __name__ == "__main__":
    sys.exit(main())
