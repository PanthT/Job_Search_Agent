"""Extract public contact channels from listed company careers pages."""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

from member_career_search import _fetch_page


EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w.-])",
    re.IGNORECASE,
)
PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:\+?1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?"
    r"\d{3}[\s.-]?\d{4}(?!\d)"
)
CONTACT_PATTERN = re.compile(
    r"\b(contact|recruit|talent acquisition|human resources|hr)\b",
    re.IGNORECASE,
)
OUTPUT_FIELDS = [
    "association",
    "member",
    "likely_company_homepage",
    "career_page_urls",
    "career_contact_emails",
    "career_contact_phones",
    "career_contact_page_urls",
    "career_contact_status",
    "career_contact_error",
]


class ContactPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.links: list[dict[str, str]] = []
        self._href = ""
        self._link_text: list[str] = []
        self._skip_depth = 0

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        if tag in {"script", "style", "noscript", "template"}:
            self._skip_depth += 1
        if tag == "a":
            self._href = attributes.get("href") or ""
            self._link_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "template"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "a" and self._href:
            self.links.append(
                {
                    "href": self._href,
                    "text": " ".join("".join(self._link_text).split()),
                }
            )
            self._href = ""
            self._link_text = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self.text.append(data)
        if self._href:
            self._link_text.append(data)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract public contact channels from company careers pages."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("likely_company_homepages.csv"),
        help="Input company CSV (default: likely_company_homepages.csv)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("company_career_contacts.csv"),
        help="Output contacts CSV (default: company_career_contacts.csv)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process at most this many rows; 0 processes all (default: 0).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.25,
        help="Minimum interval between page fetch starts (default: 0.25 seconds).",
    )
    return parser


def _career_urls(value: str) -> list[str]:
    urls: list[str] = []
    for candidate in value.split("|"):
        url = candidate.strip()
        parsed = urlparse(url)
        if (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and url not in urls
        ):
            urls.append(url)
    return urls


def _extract_contacts(
    page_url: str,
    parser: ContactPageParser,
) -> tuple[list[str], list[str], list[str]]:
    visible_text = " ".join(parser.text)
    emails = list(dict.fromkeys(match.casefold() for match in EMAIL_PATTERN.findall(visible_text)))
    phones: list[str] = []
    contact_pages: list[str] = []

    for link in parser.links:
        href = link["href"].strip()
        lowered_href = href.casefold()
        if lowered_href.startswith("mailto:"):
            address = unquote(href[7:].split("?", 1)[0]).strip()
            emails.extend(EMAIL_PATTERN.findall(address))
        elif lowered_href.startswith("tel:"):
            number = unquote(href[4:].split("?", 1)[0]).strip()
            if number:
                phones.append(number)

        candidate_url = urljoin(page_url, href)
        parsed = urlparse(candidate_url)
        if (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and CONTACT_PATTERN.search(f"{link['text']} {parsed.path}")
        ):
            contact_pages.append(parsed._replace(fragment="").geturl())

    phones.extend(PHONE_PATTERN.findall(visible_text))
    return (
        list(dict.fromkeys(email.strip().casefold() for email in emails if email.strip())),
        list(dict.fromkeys(" ".join(phone.split()) for phone in phones if phone.strip())),
        list(dict.fromkeys(contact_pages)),
    )


def run(args: argparse.Namespace) -> int:
    if args.limit < 0:
        raise ValueError("--limit must be zero or a positive number")
    if args.delay < 0:
        raise ValueError("--delay must be zero or a positive number")
    if args.input.resolve() == args.output.resolve():
        raise ValueError("--input and --output must be different files")

    with args.input.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        required = {"association", "member", "likely_company_homepage", "career_page_urls"}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(
                "Input CSV is missing required column(s): "
                + ", ".join(sorted(missing))
            )
        companies = list(reader)

    if args.limit:
        companies = companies[: args.limit]

    throttle_lock = threading.Lock()
    next_start = [0.0]

    def process(item: tuple[int, dict[str, str]]) -> tuple[dict[str, str], bool]:
        _, company = item
        career_urls = _career_urls(company.get("career_page_urls") or "")
        result = {
            "association": company.get("association", ""),
            "member": company.get("member", ""),
            "likely_company_homepage": company.get("likely_company_homepage", ""),
            "career_page_urls": " | ".join(career_urls),
            "career_contact_emails": "",
            "career_contact_phones": "",
            "career_contact_page_urls": "",
            "career_contact_status": "",
            "career_contact_error": "",
        }
        if not career_urls:
            result["career_contact_status"] = "No careers page URL listed"
            return result, False

        emails: list[str] = []
        phones: list[str] = []
        contact_pages: list[str] = []
        errors: list[str] = []
        for url in career_urls:
            with throttle_lock:
                wait = next_start[0] - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                next_start[0] = time.monotonic() + args.delay
            try:
                parser = ContactPageParser()
                parser.feed(_fetch_page(url))
            except RuntimeError as exc:
                errors.append(f"{url}: {exc}")
                continue
            found_emails, found_phones, found_contact_pages = _extract_contacts(
                url, parser
            )
            emails.extend(found_emails)
            phones.extend(found_phones)
            contact_pages.extend(found_contact_pages)

        result["career_contact_emails"] = " | ".join(dict.fromkeys(emails))
        result["career_contact_phones"] = " | ".join(dict.fromkeys(phones))
        result["career_contact_page_urls"] = " | ".join(
            dict.fromkeys(contact_pages)
        )
        if errors:
            result["career_contact_status"] = (
                "Fetch incomplete" if len(errors) < len(career_urls) else "Fetch failed"
            )
            result["career_contact_error"] = " | ".join(errors)
        elif emails or phones or contact_pages:
            result["career_contact_status"] = "Public contact info found"
        else:
            result["career_contact_status"] = "No public contact info found"
        return result, bool(errors)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    errors = 0
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
                processed = executor.map(
                    process, enumerate(companies, start=1)
                )
                for index, (row, had_error) in enumerate(processed, start=1):
                    writer.writerow(row)
                    errors += had_error
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
        f"Wrote career contact results for {len(companies)} companies to "
        f"{args.output} ({errors} page-fetch errors)."
    )
    return 1 if errors else 0


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
