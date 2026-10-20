"""Filter existing member_jobs output down to company-website postings.

Keeps rows whose job_url lives on the member's own website (same
registrable domain as directory_url) or on that member's company-specific
ATS pipeline. Generic job-board / aggregator links (LinkedIn, Indeed,
Glassdoor, ...) are marked as filtered instead of deleted so you keep an
audit trail.

Usage:
    python filter_company_site_jobs.py
    python filter_company_site_jobs.py --input member_jobs.csv --output member_jobs_company_site.csv
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

import member_career_search as mcs

FILTERED_STATUS = "Filtered: job board link, not on company website"


def filter_rows(
    rows: list[dict], company_site_only: bool = True
) -> tuple[list[dict], Counter]:
    """Mark job-board rows as filtered; return (rows, stats)."""
    stats: Counter = Counter()
    for row in rows:
        job_url = (row.get("job_url") or "").strip()
        company_site = (row.get("directory_url") or "").strip()
        if (
            company_site_only
            and job_url
            and row.get("status") == "Found"
            and not mcs.is_company_job_url(job_url, company_site, True)
        ):
            row["status"] = FILTERED_STATUS
            row["company_verification"] = "Filtered: job board link"
            stats["filtered_job_board"] += 1
        elif row.get("status") == "Found" and job_url:
            stats["kept_company_site"] += 1
        else:
            stats[f"kept_{row.get('status', 'unknown')[:30]}"] += 1
    return rows, stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Filter member_jobs.csv to company-website postings only."
    )
    parser.add_argument("--input", type=Path, default=Path("member_jobs.csv"))
    parser.add_argument(
        "--output", type=Path, default=Path("member_jobs_company_site.csv")
    )
    parser.add_argument(
        "--drop-filtered",
        action="store_true",
        help="Remove filtered rows instead of keeping them marked.",
    )
    parser.add_argument(
        "--no-company-site-only",
        dest="company_site_only",
        action="store_false",
        help="Pass through all rows unchanged (audit mode).",
    )
    parser.set_defaults(company_site_only=True)
    args = parser.parse_args()

    with args.input.open(encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        rows = list(reader)
    rows, stats = filter_rows(rows, args.company_site_only)
    if args.drop_filtered:
        kept = [row for row in rows if row.get("status") != FILTERED_STATUS]
        stats["dropped"] = len(rows) - len(kept)
        rows = kept
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(dict(stats))
    print(f"Wrote {len(rows)} rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
