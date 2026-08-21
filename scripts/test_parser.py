#!/usr/bin/env python3
"""Score parse_qol.py against hand-verified expected values.

tests/fixtures/expected.csv holds values read off the QuantumOnline pages by hand
for a sample chosen to span every archetype in the universe: baby bonds, TRUPS,
fix-to-float on both LIBOR and SOFR, a true floater, a $1,000-par convertible,
a $100-par utility preferred, and a Canadian-dollar issue.

A blank in the fixture means "not checked", not "expected empty" -- only
populated cells are compared. Exit code is non-zero if accuracy on any field
falls below --threshold, so this can gate CI after a parser change.
"""
from __future__ import annotations
import argparse, csv, sys
from pathlib import Path

FIX = Path("tests/fixtures/expected.csv")
GOT = Path("data/facts.csv")


def norm(field: str, v: str) -> str:
    v = (v or "").strip()
    if v == "":
        return ""
    low = v.lower()
    if low in ("true", "false"):
        return low
    try:
        return f"{float(v.replace('$','').replace(',','')):.4f}".rstrip("0").rstrip(".")
    except ValueError:
        return low


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.90)
    a = ap.parse_args()
    if not GOT.exists():
        print(f"missing {GOT} -- run scripts/parse_qol.py first", file=sys.stderr)
        return 1

    got = {r["symbol"]: r for r in csv.DictReader(GOT.open())}
    exp = list(csv.DictReader(FIX.open()))
    fields = [f for f in exp[0] if f != "symbol"]
    stats = {f: [0, 0] for f in fields}      # [correct, checked]
    misses: list[str] = []
    absent = 0

    for e in exp:
        g = got.get(e["symbol"])
        if not g:
            absent += 1
            misses.append(f"  {e['symbol']:<8} NOT IN data/facts.csv (harvest gap)")
            continue
        for f in fields:
            want = norm(f, e[f])
            if want == "":
                continue
            have = norm(f, g.get(f, ""))
            stats[f][1] += 1
            if have == want:
                stats[f][0] += 1
            else:
                misses.append(f"  {e['symbol']:<8} {f:<18} expected {e[f]!r:<16} got {g.get(f,'')!r}")

    print(f"fixtures: {len(exp)}  matched to output: {len(exp)-absent}\n")
    print(f"{'field':<20}{'correct':>9}{'checked':>9}{'accuracy':>10}")
    worst = 1.0
    for f in fields:
        c, n = stats[f]
        if not n:
            continue
        acc = c / n
        worst = min(worst, acc)
        mark = "" if acc >= a.threshold else "   <-- below threshold"
        print(f"{f:<20}{c:>9}{n:>9}{acc:>9.0%}{mark}")
    if misses:
        print(f"\n{len(misses)} mismatches:")
        for m in misses[:60]:
            print(m)
        if len(misses) > 60:
            print(f"  ... and {len(misses)-60} more")
    print(f"\nworst field accuracy: {worst:.0%} (threshold {a.threshold:.0%})")
    return 0 if worst >= a.threshold and not absent else 1


if __name__ == "__main__":
    sys.exit(main())
