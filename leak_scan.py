#!/usr/bin/env python3
"""Leak scan: grep content/ and docs/ for denylisted names and sensitive patterns.
The denylist and regex patterns are confidential and live OUTSIDE this repo
(../pe-trends-raw/denylist.txt and ../pe-trends-raw/scan_patterns.txt).
Exit code 1 if any hit."""
import re, sys, pathlib
ROOT = pathlib.Path(__file__).parent
DENY = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ROOT.parent / "pe-trends-raw" / "denylist.txt")
terms = [t.strip() for t in DENY.read_text(encoding="utf-8").splitlines() if t.strip()]
PATS = DENY.parent / "scan_patterns.txt"
patterns = [l.strip() for l in PATS.read_text(encoding="utf-8").splitlines() if l.strip()]
allow = [a for a in (ROOT / "leak_allowlist.txt").read_text(encoding="utf-8").splitlines() if a and not a.startswith("#")]
files = [p for p in list((ROOT / "content").rglob("*")) + list((ROOT / "docs").rglob("*")) if p.is_file() and p.name != "leak_allowlist.txt"]
hits = []
for f in files:
    for n, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
        if any(a in line for a in allow):
            continue
        for t in terms:
            if t in line: hits.append((f, n, "DENY", t, line))
        for p in patterns:
            m = re.search(p, line, re.I)
            if m: hits.append((f, n, "PATTERN", m.group(0), line))
for f, n, kind, t, line in hits:
    i = line.find(t); ctx = line[max(0, i - 30): i + 40] if i >= 0 else line[:70]
    print(f"{f.relative_to(ROOT)}:{n}: [{kind}] {t} :: …{ctx}…")
print(f"files scanned: {len(files)}, denylist terms: {len(terms)}, patterns: {len(patterns)}, hits: {len(hits)}")
sys.exit(1 if hits else 0)
