import csv
from urllib.parse import urlparse
from collections import Counter

GENERIC_HOSTS = {"linkedin.com", "indeed.com", "workopolis.com",
                 "glassdoor.com", "simplyhired.com", "ziprecruiter.com"}

def host(u):
    try:
        return (urlparse(u).hostname or "").lower().removeprefix("www.")
    except Exception:
        return ""

def base(h):
    parts = h.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else h

with open("member_jobs_verified.csv", encoding="utf-8-sig") as f:
    rows = list(csv.DictReader(f))

real = []
for r in rows:
    if not (r["verified_on_company_website"].startswith("Verified")
            and r["status"] == "Found"
            and r["job_title"].strip() and r["job_url"].strip()):
        continue
    dh, ch, jh = host(r["directory_url"]), host(r["careers_page_url"]), host(r["job_url"])
    if not dh:
        continue
    if jh in GENERIC_HOSTS or any(jh.endswith("." + g) for g in GENERIC_HOSTS):
        continue
    if ch in GENERIC_HOSTS or any(ch.endswith("." + g) for g in GENERIC_HOSTS):
        continue
    # job must live on company domain (or same registrable domain), OR
    # careers page on company domain with job linked from it
    if base(jh) == base(dh):
        real.append(r)
    elif ch and base(ch) == base(dh):
        real.append(r)

print("genuinely verified on company website:", len(real))
print(Counter(r["member"] for r in real).most_common(25))
for r in real[:20]:
    print("-", r["member"], "|", r["job_title"][:70])
    print("  job:", r["job_url"][:110])
    print("  site:", r["directory_url"][:110])

with open("member_jobs_strict_verified.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(real)
print("Wrote member_jobs_strict_verified.csv with", len(real), "rows")

