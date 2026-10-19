"""Verify member jobs by visiting the actual company website (directory_url)."""
from __future__ import annotations
import argparse
import csv
from pathlib import Path
from urllib.parse import urlparse

import member_career_search as mcs


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").casefold().removeprefix("www.")
    except Exception:
        return ""


def _fetch_parser(url: str):
    parser = mcs.JobPageParser()
    parser.feed(mcs._fetch_page(url))
    return parser
def verify_row(row: dict, site_cache: dict, fast: bool = False) -> dict:
    member = row.get("member", "")
    directory_url = (row.get("directory_url") or "").strip()
    careers_url = (row.get("careers_page_url") or "").strip()
    job_url = (row.get("job_url") or "").strip()
    out = dict(row)
    out["website_fetch"] = ""
    out["website_mentions_company"] = ""
    out["website_links_to_careers"] = ""
    out["careers_page_recheck"] = ""
    out["job_page_recheck"] = ""
    out["job_page_mentions_company"] = ""
    out["job_page_mentions_title"] = ""
    out["verified_on_company_website"] = "Not verified"
    if directory_url:
        site = site_cache.get(directory_url)
        if site is None:
            try:
                parser = _fetch_parser(directory_url)
                links = " ".join(l.get("href", "") for l in parser.links)
                site = {"parser": parser, "links": links, "error": ""}
            except RuntimeError as exc:
                site = {"parser": None, "links": "", "error": str(exc)}
            site_cache[directory_url] = site
        if site.get("error"):
            out["website_fetch"] = site["error"][:300]
        else:
            out["website_fetch"] = "OK"
            out["website_mentions_company"] = mcs.verify_company_on_page(
                member, site["parser"], directory_url)
            linked = ""
            for u in (careers_url, job_url):
                h = _host(u)
                if h and h in site["links"].casefold():
                    linked = "Yes - links to " + h
                    break
            if not linked and careers_url and _host(careers_url) == _host(directory_url):
                linked = "Yes - same host as company site"
            if not linked and job_url and _host(job_url) == _host(directory_url):
                linked = "Yes - same host as company site"
            out["website_links_to_careers"] = linked or "No link found"
    if careers_url and not fast:
        try:
            out["careers_page_recheck"] = mcs.verify_company_on_page(
                member, _fetch_parser(careers_url), careers_url)
        except RuntimeError as exc:
            out["careers_page_recheck"] = str(exc)[:300]
    elif careers_url:
        out["careers_page_recheck"] = row.get("career_page_company_verification", "")
    if job_url and not fast:
        try:
            parser = _fetch_parser(job_url)
            out["job_page_recheck"] = "Reachable"
            out["job_page_mentions_company"] = mcs.verify_company_on_page(
                member, parser, job_url)
            title = (row.get("job_title") or "").strip()
            page_text = " ".join(parser.text + parser.headings).casefold()
            if title and len(title) > 3:
                key = " ".join(title.split()[:4]).casefold()
                out["job_page_mentions_title"] = "Yes" if key in page_text else "No"
            else:
                out["job_page_mentions_title"] = "No title to check"
        except RuntimeError as exc:
            out["job_page_recheck"] = str(exc)[:300]
    elif job_url:
        out["job_page_recheck"] = row.get("status", "")
        out["job_page_mentions_company"] = row.get("company_verification", "")
    ok = {"Found in structured organization data", "Found in page content", "Found in domain"}
    site_ok = out["website_mentions_company"] in ok
    job_ok = out["job_page_mentions_company"] in ok
    career_ok = out["careers_page_recheck"] in ok
    if not directory_url:
        out["verified_on_company_website"] = "Not verified - no company website"
    elif site_ok and (job_ok or career_ok):
        out["verified_on_company_website"] = "Verified - company site + job page confirm"
    elif site_ok:
        out["verified_on_company_website"] = "Partially verified - site confirms, job page does not"
    elif job_ok or career_ok:
        out["verified_on_company_website"] = "Partially verified - job page confirms, site homepage does not"
    else:
        out["verified_on_company_website"] = "Not verified - company not found"
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify member_jobs.csv on actual company websites.")
    ap.add_argument("--input", type=Path, default=Path("member_jobs.csv"))
    ap.add_argument("--output", type=Path, default=Path("member_jobs_verified.csv"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--fast", action="store_true",
                    help="Only visit company websites (directory_url); reuse "
                         "existing careers/job verification from input CSV.")
    args = ap.parse_args()
    with args.input.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if args.limit:
        rows = rows[:args.limit]
    extra = ["website_fetch", "website_mentions_company", "website_links_to_careers",
             "careers_page_recheck", "job_page_recheck", "job_page_mentions_company",
             "job_page_mentions_title", "verified_on_company_website"]
    site_cache: dict = {}
    verified = []
    for i, row in enumerate(rows, 1):
        verified.append(verify_row(row, site_cache, fast=args.fast))
        if i % 50 == 0:
            print(f"  {i}/{len(rows)}", flush=True)
    with args.output.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) + extra)
        w.writeheader()
        w.writerows(verified)
    from collections import Counter
    print(Counter(v["verified_on_company_website"] for v in verified))
    print(f"Wrote {len(verified)} rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
