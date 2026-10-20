"""Re-fetch job descriptions for the strict-verified CSV using the fixed parser.

Prefers JobPosting JSON-LD description; falls back to visible page text
with CSS/JS boilerplate stripped. Overwrites member_jobs_strict_verified.csv
in place (with .bak backup) and dedups.
"""
import csv
import re
import shutil
from pathlib import Path

import member_career_search as mcs

BASE = Path(__file__).resolve().parent
SRC = BASE / "member_jobs_strict_verified.csv"

CSS_JS_RE = re.compile(
    r"(window\.|\(function|function\s+\w+\(|var\s+\w+\s*=|"
    r"\.css-[a-z0-9]+\{|img:is\(|contain-intrinsic-size|"
    r"gdprApplies|dataLayer|gtm\.start|kendo\.culture|__RequestVerif)",
    re.IGNORECASE,
)


def clean_text(chunks):
    out = []
    for c in chunks:
        c = " ".join(c.split())
        if len(c) < 40:
            continue
        if CSS_JS_RE.search(c):
            continue
        # skip CSS rule blobs
        if c.count("{") >= 1 and c.count(";") >= 2:
            continue
        out.append(c)
    return " ".join(out)


def fetch_description(job_url, fallback_title):
    parser = mcs.JobPageParser()
    raw = mcs._fetch_page(job_url)
    if raw[:5] == "%PDF-":
        raise RuntimeError("job link is a PDF, not an HTML posting")
    parser.feed(raw)
    postings = mcs._job_postings_from_page(parser)
    for p in postings:
        d = mcs._plain_text(p.get("description", ""))
        d = clean_text([d])
        if len(d) >= 80:
            return d[: mcs.MAX_DESCRIPTION_LENGTH]
    d = clean_text(parser.text)
    if len(d) >= 80:
        return d[: mcs.MAX_DESCRIPTION_LENGTH]
    # last resort: meta description
    for m in parser.metadata:
        m = " ".join(m.split())
        if len(m) >= 80 and not CSS_JS_RE.search(m):
            return m[: mcs.MAX_DESCRIPTION_LENGTH]
    return d[: mcs.MAX_DESCRIPTION_LENGTH]


def main():
    with SRC.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = list(reader)
    ok, junk, failed = 0, 0, 0
    for i, r in enumerate(rows, 1):
        url = (r.get("job_url") or "").strip()
        if not url:
            failed += 1
            continue
        try:
            d = fetch_description(url, r.get("job_title", ""))
        except Exception as exc:
            print(f"  [{i}] FAIL {r.get('member','')} {url[:80]}: {exc}")
            failed += 1
            continue
        if len(d) < 80 or CSS_JS_RE.search(d[:500] + d[-500:]):
            junk += 1
            marker = " [description unavailable - see job link]"
            r["description"] = (d[:500] + marker) if d else (
                f"See job posting at {url}" + marker)
        else:
            ok += 1
            r["description"] = d
            # refresh experience/salary from clean text
            r["years_of_experience"] = mcs._experience_from_posting({}, d)
            sal = mcs._salary_from_text(d)
            if sal:
                r["salary"] = sal
        if i % 25 == 0:
            print(f"  {i}/{len(rows)} ok={ok} junk={junk} failed={failed}", flush=True)
    shutil.copy(SRC, str(SRC) + ".bak")
    with SRC.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"Done: ok={ok} junk={junk} failed={failed} of {len(rows)}")


if __name__ == "__main__":
    main()

