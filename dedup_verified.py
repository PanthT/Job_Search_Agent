"""Remove redundant rows from verified CSVs.

Dedup keys (in priority order):
  1. Exact duplicate rows.
  2. Same normalized (member, job_title, job_url) - repeat crawl of same posting.
  3. Same non-empty job_url appearing under different member names
     (same posting found via two member searches) - keep first.
  4. Empty rows (no title and no url): same (member, careers_page_url).
Keeps first occurrence, preserves order. Writes .bak backup before overwriting.
"""
import csv
import shutil
from pathlib import Path

BASE = Path(__file__).resolve().parent


def norm(s):
    return " ".join((s or "").split()).casefold()


def dedup(rows):
    seen_exact = set()
    seen_mtu = set()
    seen_url = set()
    seen_empty = set()
    kept, removed = [], 0
    for r in rows:
        t = tuple(sorted(r.items()))
        if t in seen_exact:
            removed += 1
            continue
        seen_exact.add(t)
        title, url = norm(r.get("job_title", "")), (r.get("job_url", "") or "").strip()
        member = norm(r.get("member", ""))
        if not title and not url:
            k = (member, (r.get("careers_page_url", "") or "").strip())
            if k in seen_empty:
                removed += 1
                continue
            seen_empty.add(k)
            kept.append(r)
            continue
        mtu = (member, title, url)
        if mtu in seen_mtu:
            removed += 1
            continue
        seen_mtu.add(mtu)
        if url:
            if url in seen_url:
                removed += 1
                continue
            seen_url.add(url)
        kept.append(r)
    return kept, removed


for fn in ["member_jobs_verified.csv", "member_jobs_strict_verified.csv"]:
    p = BASE / fn
    with p.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
        fields = f.fieldnames if hasattr(f, "fieldnames") else None
    # re-read fieldnames properly
    with p.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = list(reader)
    kept, removed = dedup(rows)
    shutil.copy(p, str(p) + ".bak")
    with p.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(kept)
    print(f"{fn}: {len(rows)} -> {len(kept)} (removed {removed}); backup at {fn}.bak")
