import csv
from collections import Counter

for fn in ["member_jobs_verified.csv", "member_jobs_strict_verified.csv"]:
    with open(fn, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    print("===", fn, "rows:", len(rows))
    seen = set()
    exact = 0
    for r in rows:
        t = tuple(sorted(r.items()))
        if t in seen:
            exact += 1
        else:
            seen.add(t)
    print(" exact dup rows:", exact)
    urls = [r.get("job_url", "").strip() for r in rows if r.get("job_url", "").strip()]
    c = Counter(urls)
    print(" unique urls:", len(c),
          " duplicated urls:", sum(1 for v in c.values() if v > 1),
          " extra rows:", sum(v - 1 for v in c.values() if v > 1))
    keys = [(r.get("member", "").strip().lower(),
             r.get("job_title", "").strip().lower(),
             r.get("job_url", "").strip()) for r in rows]
    ck = Counter(keys)
    print(" dup member+title+url keys:", sum(1 for v in ck.values() if v > 1))
    for k, v in ck.most_common(5):
        if v > 1:
            print("  ", v, "x", k[0][:40], "|", k[1][:50], "|", k[2][:70])
    # empty-job rows (no title and no url) duplicated per member+careers page?
    empty = [r for r in rows if not r.get("job_title", "").strip() and not r.get("job_url", "").strip()]
    print(" empty title+url rows:", len(empty))
