"""Verify member jobs by visiting the member's company website."""
from __future__ import annotations
import argparse
import csv
from pathlib import Path
from urllib.parse import urlparse

import member_career_search as mcs

EXCLUDED_OUTPUT_COLUMNS = {"careers_page_url", "job_url"}


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").casefold().removeprefix("www.")
    except Exception:
        return ""


def _parse(page: str):
    parser = mcs.JobPageParser()
    parser.feed(page)
    return parser


def _fetch_parser(url: str):
    try:
        return _parse(mcs._fetch_page(url))
    except RuntimeError:
        raise
    except Exception as exc:
        # Network-level failures (http.client.IncompleteRead,
        # RemoteDisconnected, socket errors, SSL errors, ...) must not kill
        # a multi-thousand-row verification run; surface them as fetch errors.
        raise RuntimeError(f"Could not fetch {url}: {exc}") from exc


def verify_row(row: dict, site_cache: dict) -> dict:
    member = row.get("member", "")
    directory_url = (
        row.get("member_website") or row.get("directory_url") or ""
    ).strip()
    careers_url = (row.get("careers_page_url") or "").strip()
    job_url = (row.get("job_url") or "").strip()
    out = dict(row)
    for column in EXCLUDED_OUTPUT_COLUMNS:
        out.pop(column, None)
    out["member_website"] = directory_url
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
            for url in (careers_url, job_url):
                host = _host(url)
                if host and host in site["links"].casefold():
                    linked = "Yes - links to " + host
                    break
            if not linked and careers_url and _host(careers_url) == _host(directory_url):
                linked = "Yes - same host as company site"
            if not linked and job_url and _host(job_url) == _host(directory_url):
                linked = "Yes - same host as company site"
            out["website_links_to_careers"] = linked or "No link found"
    ok = {"Found in structured organization data", "Found in page content", "Found in domain"}
    site_ok = out["website_mentions_company"] in ok
    if not directory_url:
        out["verified_on_company_website"] = "Not verified - no company website"
    elif site_ok:
        out["verified_on_company_website"] = "Verified - member website confirms"
    else:
        out["verified_on_company_website"] = "Not verified - company not found"
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Verify member_jobs.csv on the member companies' websites."
    )
    ap.add_argument("--input", type=Path, default=Path("member_jobs.csv"))
    ap.add_argument("--output", type=Path, default=Path("member_jobs_verified.csv"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument(
        "--fast",
        action="store_true",
        help="Legacy option; verification now always visits only member websites.",
    )
    ap.add_argument("--resume", action="store_true",
                    help="Skip rows already present in the output file "
                         "(match on member+job_title+job_url) and append new "
                         "results, so an interrupted run can continue.")
    args = ap.parse_args()
    with args.input.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if args.limit:
        rows = rows[:args.limit]
    extra = ["website_fetch", "website_mentions_company", "website_links_to_careers",
             "careers_page_recheck", "job_page_recheck", "job_page_mentions_company",
             "job_page_mentions_title", "verified_on_company_website"]
    fieldnames = [
        column for column in rows[0].keys()
        if column not in EXCLUDED_OUTPUT_COLUMNS
    ]
    if "member_website" not in fieldnames:
        fieldnames.append("member_website")
    fieldnames.extend(extra)
    site_cache: dict = {}
    if args.resume and args.output.exists():
        with args.output.open(encoding="utf-8-sig") as f:
            done = list(csv.DictReader(f))
        done_keys = {
            ((r.get("member") or ""), (r.get("job_title") or ""),
             (r.get("job_url") or "")) for r in done
        }
        pending = [
            r for r in rows
            if ((r.get("member") or ""), (r.get("job_title") or ""),
                (r.get("job_url") or "")) not in done_keys
        ]
        print(f"Resuming: {len(done)} rows already verified, "
              f"{len(pending)} remaining.", flush=True)
        if pending and list(done and done[0].keys() or []) != fieldnames:
            print("Warning: existing output columns differ; "
                  "appending with current columns.", flush=True)
        verified = [
            {column: row.get(column, "") for column in fieldnames}
            for row in done
        ]
        rows = pending
    else:
        verified = []
    checkpoint_every = 50
    for i, row in enumerate(rows, 1):
        try:
            verified.append(verify_row(row, site_cache))
        except Exception as exc:  # last-resort guard: never kill the run
            fallback = {
                column: value
                for column, value in row.items()
                if column not in EXCLUDED_OUTPUT_COLUMNS
            }
            for column in extra:
                fallback[column] = ""
            fallback["website_fetch"] = f"Unexpected error: {exc}"[:300]
            fallback["verified_on_company_website"] = "Not verified"
            verified.append(fallback)
        if i % checkpoint_every == 0:
            print(f"  {i}/{len(rows)}", flush=True)
            with args.output.open("w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=fieldnames)
                w.writeheader()
                w.writerows(verified)
    with args.output.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(verified)
    from collections import Counter
    print(Counter(v["verified_on_company_website"] for v in verified))
    print(f"Wrote {len(verified)} rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
