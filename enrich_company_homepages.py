"""Add career-page and LinkedIn links to the likely company homepages CSV."""

from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from member_career_search import _create_web_search, find_company_linkedin


ADDED_FIELDS = [
    "career_page_urls",
    "career_page_status",
    "linkedin_verification",
    "enrichment_error",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Add career-page results and matching LinkedIn company pages to "
            "the likely company homepages CSV."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("likely_company_homepages.csv"),
        help="CSV to enrich in place (default: likely_company_homepages.csv)",
    )
    parser.add_argument(
        "--career-results",
        type=Path,
        default=Path("company_career_pages.csv"),
        help="Career-page results CSV (default: company_career_pages.csv)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Look up LinkedIn pages for at most this many rows; 0 means all.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Minimum interval between LinkedIn searches (default: 1 second).",
    )
    parser.add_argument(
        "--retry-no-match",
        action="store_true",
        help="Also retry rows previously marked No matching page found.",
    )
    return parser


def _cache_career_results(path: Path) -> dict[tuple[str, str, str], dict[str, list[str]]]:
    required = {"association", "member", "likely_company_homepage", "status"}
    grouped: dict[tuple[str, str, str], dict[str, list[str]]] = defaultdict(
        lambda: {"urls": [], "statuses": [], "errors": []}
    )
    with path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(
                "Career results CSV is missing required column(s): "
                + ", ".join(sorted(missing))
            )
        for row in reader:
            key = (
                row.get("association", "").strip(),
                row.get("member", "").strip(),
                row.get("likely_company_homepage", "").strip(),
            )
            entry = grouped[key]
            url = (row.get("careers_page_url") or "").strip()
            if url and url not in entry["urls"]:
                entry["urls"].append(url)
            status = (row.get("status") or "").strip()
            if status and status not in entry["statuses"]:
                entry["statuses"].append(status)
            error = (row.get("search_error") or "").strip()
            if error and error not in entry["errors"]:
                entry["errors"].append(error)
    return grouped


def run(args: argparse.Namespace) -> int:
    if args.limit < 0:
        raise ValueError("--limit must be zero or a positive number")
    if args.delay < 0:
        raise ValueError("--delay must be zero or a positive number")
    if args.input.resolve() == args.career_results.resolve():
        raise ValueError("--input and --career-results must be different files")

    career_results = _cache_career_results(args.career_results)
    with args.input.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        fieldnames = list(reader.fieldnames or [])
        required = {
            "association",
            "member",
            "likely_company_homepage",
            "linkedin_company_page",
        }
        missing = required.difference(fieldnames)
        if missing:
            raise ValueError(
                "Input CSV is missing required column(s): "
                + ", ".join(sorted(missing))
            )
        rows = list(reader)

    for field in ADDED_FIELDS:
        if field not in fieldnames:
            fieldnames.append(field)

    indexes_to_search = [
        index
        for index, row in enumerate(rows)
        if not (row.get("linkedin_company_page") or "").strip()
        and (
            not (row.get("linkedin_verification") or "").strip()
            or row.get("linkedin_verification") == "Search failed"
            or (
                getattr(args, "retry_no_match", False)
                and row.get("linkedin_verification") == "No matching page found"
            )
        )
    ]
    if args.limit:
        indexes_to_search = indexes_to_search[: args.limit]
    search = _create_web_search() if indexes_to_search else None
    throttle_lock = threading.Lock()
    next_start = [0.0]

    def lookup(item: tuple[int, dict[str, str]]) -> tuple[int, dict[str, str], str]:
        index, row = item
        member = (row.get("member") or "").strip()
        if not member:
            return index, {"url": "", "verification": "Missing company name"}, (
                "The member column is blank."
            )
        with throttle_lock:
            wait = next_start[0] - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            next_start[0] = time.monotonic() + args.delay
        try:
            assert search is not None
            return index, find_company_linkedin(search, member), ""
        except RuntimeError as exc:
            return index, {"url": "", "verification": "Search failed"}, str(exc)

    errors = 0
    temporary_path: Path | None = None
    search_results: dict[int, tuple[dict[str, str], str]] = {}
    if indexes_to_search:
        with ThreadPoolExecutor(max_workers=1) as executor:
            for completed, (index, result, error) in enumerate(
                executor.map(
                    lookup,
                    ((index, rows[index]) for index in indexes_to_search),
                ),
                start=1,
            ):
                search_results[index] = (result, error)
                errors += bool(error)
                if completed % 25 == 0 or completed == len(indexes_to_search):
                    print(
                        f"LinkedIn searches: {completed}/{len(indexes_to_search)}",
                        file=sys.stderr,
                        flush=True,
                    )

    for index, row in enumerate(rows):
        error_parts: list[str] = []
        key = (
            (row.get("association") or "").strip(),
            (row.get("member") or "").strip(),
            (row.get("likely_company_homepage") or "").strip(),
        )
        career = career_results.get(key)
        if career:
            if career["urls"]:
                row["career_page_urls"] = " | ".join(career["urls"])
            if career["statuses"]:
                row["career_page_status"] = " | ".join(career["statuses"])
            if career["errors"]:
                error_parts.extend(career["errors"])
                errors += 1

        if index in search_results:
            result, error = search_results[index]
            if result.get("url"):
                row["linkedin_company_page"] = result["url"]
            row["linkedin_verification"] = result.get("verification", "")
            if error:
                error_parts.append(error)
            row["enrichment_error"] = " | ".join(error_parts)
        elif career and career["errors"]:
            row["enrichment_error"] = " | ".join(error_parts)
        elif (row.get("linkedin_company_page") or "").strip():
            row["linkedin_verification"] = (
                row.get("linkedin_verification") or "Existing URL"
            )

    try:
        with tempfile.NamedTemporaryFile(
            "w",
            newline="",
            encoding="utf-8-sig",
            dir=args.input.parent,
            prefix=f".{args.input.name}.",
            suffix=".tmp",
            delete=False,
        ) as destination:
            temporary_path = Path(destination.name)
            writer = csv.DictWriter(destination, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        assert temporary_path is not None
        os.replace(temporary_path, args.input)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()

    with_career_pages = sum(bool(row.get("career_page_urls")) for row in rows)
    with_linkedin = sum(bool(row.get("linkedin_company_page")) for row in rows)
    print(
        f"Updated {args.input}: {with_career_pages} rows with career pages, "
        f"{with_linkedin} with LinkedIn company pages; {errors} errors."
    )
    return 1 if errors else 0


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
