"""Find likely careers pages for companies with known homepages."""

from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlparse

from member_career_search import (
    CAREER_TERMS,
    DIRECT_POSTING_PATHS,
    JobPageParser,
    _create_web_search,
    _fetch_page,
    find_career_pages,
    is_job_board_url,
)


OUTPUT_FIELDS = [
    "association",
    "member",
    "likely_company_homepage",
    "careers_page_url",
    "search_result_title",
    "search_snippet",
    "status",
    "search_error",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Search for company careers pages from a homepage CSV."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("likely_company_homepages.csv"),
        help="Input CSV (default: likely_company_homepages.csv)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("company_career_pages.csv"),
        help="Output CSV (default: company_career_pages.csv)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Search at most this many companies; 0 searches all (default: 0).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Seconds to wait between company searches (default: 1).",
    )
    return parser


def _valid_homepage(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def _find_homepage_career_links(homepage: str) -> list[dict[str, str]]:
    parser = JobPageParser()
    parser.feed(_fetch_page(homepage))
    results: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for link in parser.links:
        url = urljoin(homepage, link["href"])
        parsed = urlparse(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or is_job_board_url(url)
            or DIRECT_POSTING_PATHS.search(parsed.path)
            or not CAREER_TERMS.search(f"{link['text']} {parsed.path}")
        ):
            continue
        normalized_url = parsed._replace(fragment="").geturl()
        if normalized_url in seen_urls:
            continue
        seen_urls.add(normalized_url)
        results.append(
            {
                "url": normalized_url,
                "title": link["text"] or normalized_url,
                "snippet": "Link discovered on the company homepage.",
            }
        )
        if len(results) == 3:
            break
    return results


def _output_row(
    company: dict[str, str],
    *,
    url: str = "",
    title: str = "",
    snippet: str = "",
    status: str,
    error: str = "",
) -> dict[str, str]:
    return {
        "association": company.get("association", ""),
        "member": company.get("member", ""),
        "likely_company_homepage": company.get("likely_company_homepage", ""),
        "careers_page_url": url,
        "search_result_title": title,
        "search_snippet": snippet,
        "status": status,
        "search_error": error,
    }


def run(args: argparse.Namespace) -> int:
    if args.limit < 0:
        raise ValueError("--limit must be zero or a positive number")
    if args.delay < 0:
        raise ValueError("--delay must be zero or a positive number")
    if args.input.resolve() == args.output.resolve():
        raise ValueError("--input and --output must be different files")

    with args.input.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        required_fields = {"association", "member", "likely_company_homepage"}
        missing_fields = required_fields.difference(reader.fieldnames or [])
        if missing_fields:
            fields = ", ".join(sorted(missing_fields))
            raise ValueError(f"Input CSV is missing required column(s): {fields}")
        companies = list(reader)

    if args.limit:
        companies = companies[: args.limit]
    search = _create_web_search() if companies else None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    errors = 0
    temporary_path: Path | None = None
    throttle_lock = threading.Lock()
    next_start = [0.0]

    def process_company(
        item: tuple[int, dict[str, str]],
    ) -> tuple[list[dict[str, str]], int]:
        _, company = item
        with throttle_lock:
            wait = next_start[0] - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            next_start[0] = time.monotonic() + args.delay

        member = (company.get("member") or "").strip()
        homepage = (company.get("likely_company_homepage") or "").strip()
        if not member:
            return [
                _output_row(
                    company,
                    status="Invalid input: missing company name",
                    error="The member column is blank.",
                )
            ], 1
        if not _valid_homepage(homepage):
            return [
                _output_row(
                    company,
                    status="Invalid input: missing or invalid homepage",
                    error="Expected an http:// or https:// homepage URL.",
                )
            ], 1

        errors_for_company = 0
        homepage_error = ""
        try:
            results = _find_homepage_career_links(homepage)
        except RuntimeError as exc:
            results = []
            homepage_error = f"Homepage fetch failed: {exc}"
            errors_for_company += 1

        result_source = "homepage"
        search_error = ""
        if not results:
            try:
                assert search is not None
                results = find_career_pages(
                    search,
                    member,
                    company_website_url=homepage,
                    company_site_only=True,
                )
                result_source = "search"
            except RuntimeError as exc:
                search_error = str(exc)
                errors_for_company += 1
                results = []

        if results:
            rows = [
                _output_row(
                    company,
                    url=result["url"],
                    title=result["title"],
                    snippet=result["snippet"],
                    status=(
                        "Found on company homepage; destination "
                        "not independently verified"
                        if result_source == "homepage"
                        else "Search result; not independently verified"
                    ),
                    error=homepage_error,
                )
                for result in results
            ]
            return rows, errors_for_company

        if search_error:
            return [
                _output_row(
                    company,
                    status="Search failed",
                    error="; ".join(
                        part for part in (homepage_error, search_error) if part
                    ),
                )
            ], errors_for_company
        return [
            _output_row(
                company,
                status="No likely careers page found",
                error=homepage_error,
            )
        ], errors_for_company

    try:
        with tempfile.NamedTemporaryFile(
            "w",
            newline="",
            encoding="utf-8-sig",
            dir=args.output.parent,
            prefix=f".{args.output.name}.",
            suffix=".tmp",
            delete=False,
        ) as destination:
            temporary_path = Path(destination.name)
            writer = csv.DictWriter(destination, fieldnames=OUTPUT_FIELDS)
            writer.writeheader()

            with ThreadPoolExecutor(max_workers=4) as executor:
                results = executor.map(
                    process_company, enumerate(companies, start=1)
                )
                for index, (company_rows, company_errors) in enumerate(
                    results, start=1
                ):
                    writer.writerows(company_rows)
                    errors += company_errors
                    if index % 25 == 0 or index == len(companies):
                        print(
                            f"Processed {index}/{len(companies)} companies",
                            file=sys.stderr,
                            flush=True,
                        )

        assert temporary_path is not None
        os.replace(temporary_path, args.output)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()

    print(
        f"Wrote careers-page results for {len(companies)} companies to "
        f"{args.output} ({errors} search or input errors)."
    )
    return 1 if errors else 0


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
