"""Build a company record for every CADSI and AIAC member."""

from __future__ import annotations

import argparse
import csv
import os
import tempfile
from pathlib import Path


BASE_FIELDS = [
    "association",
    "member",
    "likely_company_homepage",
    "verification",
    "association_directory_url",
    "linkedin_company_page",
]
ENRICHMENT_FIELDS = [
    "career_page_urls",
    "career_page_status",
    "linkedin_verification",
    "enrichment_error",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a company-homepage CSV containing every record from "
            "member_careers.csv, including rows without a verified homepage."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("member_careers.csv"),
        help="Member records CSV (default: member_careers.csv)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("likely_company_homepages.csv"),
        help="Company records CSV (default: likely_company_homepages.csv)",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    if args.input.resolve() == args.output.resolve():
        raise ValueError("--input and --output must be different files")

    with args.input.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        required = {
            "association",
            "member",
            "association_directory_url",
            "member_website",
            "company_website_verification",
        }
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(
                "Input CSV is missing required column(s): "
                + ", ".join(sorted(missing))
            )
        source_rows = list(reader)
        source_fields = reader.fieldnames or []

    existing_rows: dict[tuple[str, str], dict[str, str]] = {}
    existing_fields: list[str] = []
    if args.output.exists():
        with args.output.open(newline="", encoding="utf-8-sig") as current:
            existing_reader = csv.DictReader(current)
            existing_fields = existing_reader.fieldnames or []
            existing_rows = {
                (
                    (row.get("association") or "").strip(),
                    (row.get("member") or "").strip(),
                ): row
                for row in existing_reader
            }

    fieldnames = list(
        dict.fromkeys(
            BASE_FIELDS
            + ENRICHMENT_FIELDS
            + existing_fields
            + [
                field
                for field in source_fields
                if field
                not in {
                    "association",
                    "member",
                    "member_website",
                    "company_website_verification",
                    "association_directory_url",
                    "linkedin_company_page",
                }
            ]
        )
    )
    output_rows: list[dict[str, str]] = []
    for source_row in source_rows:
        association = (source_row.get("association") or "").strip()
        member = (source_row.get("member") or "").strip()
        prior = existing_rows.get((association, member), {})
        row = {field: "" for field in fieldnames}
        row.update(prior)
        row.update(
            {
                "association": association,
                "member": member,
                "likely_company_homepage": (
                    source_row.get("member_website")
                    or prior.get("likely_company_homepage")
                    or ""
                ).strip(),
                "verification": (
                    source_row.get("company_website_verification")
                    or prior.get("verification")
                    or ""
                ).strip(),
                "association_directory_url": (
                    source_row.get("association_directory_url")
                    or prior.get("association_directory_url")
                    or ""
                ).strip(),
                "linkedin_company_page": (
                    source_row.get("linkedin_company_page")
                    or prior.get("linkedin_company_page")
                    or ""
                ).strip(),
            }
        )
        for field in source_fields:
            if field not in {
                "association",
                "member",
                "member_website",
                "company_website_verification",
                "association_directory_url",
                "linkedin_company_page",
            }:
                row[field] = source_row.get(field, "")
        output_rows.append(row)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
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
            writer = csv.DictWriter(destination, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(output_rows)
        assert temporary_path is not None
        os.replace(temporary_path, args.output)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()

    with_homepages = sum(bool(row["likely_company_homepage"]) for row in output_rows)
    print(
        f"Wrote {len(output_rows)} member company records to {args.output}; "
        f"{with_homepages} have a homepage URL."
    )
    return 0


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
