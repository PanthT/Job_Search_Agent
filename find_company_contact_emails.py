"""Find publicly listed general and HR emails on company-owned pages."""

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

from member_career_search import _fetch_page, is_company_site_url


EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w.-])",
    re.IGNORECASE,
)
HR_PATTERN = re.compile(
    r"\b(hr|human resources?|recruit(?:er|ers|ing|ment)?|talent|"
    r"careers?|hiring|employment|jobs?)\b",
    re.IGNORECASE,
)
CONTACT_LINK_PATTERN = re.compile(
    r"\b(contact|about|team|people|human resources?|hr|recruit|talent|"
    r"careers?|jobs?|employment)\b",
    re.IGNORECASE,
)
OUTPUT_FIELDS = [
    "association",
    "member",
    "likely_company_homepage",
    "career_page_urls",
    "pages_scanned",
    "public_contact_emails",
    "hr_recruiting_emails",
    "email_source_urls",
    "contact_page_urls",
    "contact_email_status",
    "contact_email_error",
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
        description=(
            "Find publicly listed general and HR/recruiting email addresses "
            "on company homepages, careers pages, and linked contact pages."
        )
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
        default=Path("company_contact_emails.csv"),
        help="Output CSV (default: company_contact_emails.csv)",
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
        default=0.2,
        help="Minimum interval between page fetch starts (default: 0.2 seconds).",
    )
    parser.add_argument(
        "--max-pages-per-company",
        type=int,
        default=4,
        help="Maximum company-owned pages to scan per company (default: 4).",
    )
    return parser


def _urls(value: str) -> list[str]:
    result: list[str] = []
    for candidate in value.split("|"):
        url = candidate.strip()
        parsed = urlparse(url)
        if parsed.scheme in {"http", "https"} and parsed.hostname and url not in result:
            result.append(url)
    return result


def _emails_from_page(
    page_url: str,
    parser: ContactPageParser,
) -> tuple[dict[str, bool], list[str]]:
    visible_text = " ".join(parser.text)
    emails: dict[str, bool] = {}
    for match in EMAIL_PATTERN.finditer(visible_text):
        email = match.group().casefold()
        context = visible_text[max(0, match.start() - 100) : match.start()]
        context = re.split(r"[\n.!?;|]", context)[-1]
        emails[email] = emails.get(email, False) or bool(
            HR_PATTERN.search(f"{context} {email.split('@', 1)[0]}")
        )

    contact_links: list[str] = []
    for link in parser.links:
        href = link["href"].strip()
        lowered_href = href.casefold()
        if lowered_href.startswith("mailto:"):
            address = unquote(href[7:].split("?", 1)[0]).strip()
            label = f"{link['text']} {address}"
            for email in EMAIL_PATTERN.findall(address):
                normalized = email.casefold()
                emails[normalized] = emails.get(normalized, False) or bool(
                    HR_PATTERN.search(label)
                )
            continue

        candidate_url = urljoin(page_url, href)
        parsed = urlparse(candidate_url)
        if (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and is_company_site_url(candidate_url, page_url)
            and CONTACT_LINK_PATTERN.search(f"{link['text']} {parsed.path}")
        ):
            contact_links.append(parsed._replace(fragment="").geturl())

    return emails, list(dict.fromkeys(contact_links))


def run(args: argparse.Namespace) -> int:
    if args.limit < 0:
        raise ValueError("--limit must be zero or a positive number")
    if args.delay < 0:
        raise ValueError("--delay must be zero or a positive number")
    if args.max_pages_per_company <= 0:
        raise ValueError("--max-pages-per-company must be positive")
    if args.input.resolve() == args.output.resolve():
        raise ValueError("--input and --output must be different files")

    with args.input.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        required = {
            "association",
            "member",
            "likely_company_homepage",
            "career_page_urls",
        }
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

    def fetch(url: str) -> str:
        with throttle_lock:
            wait = next_start[0] - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            next_start[0] = time.monotonic() + args.delay
        return _fetch_page(url)

    def process(
        item: tuple[int, dict[str, str]],
    ) -> tuple[dict[str, str], bool]:
        _, company = item
        homepage = (company.get("likely_company_homepage") or "").strip()
        career_urls = _urls(company.get("career_page_urls") or "")
        seed_urls = _urls("|".join([homepage, *career_urls]))
        row = {
            "association": company.get("association", ""),
            "member": company.get("member", ""),
            "likely_company_homepage": homepage,
            "career_page_urls": " | ".join(career_urls),
            "pages_scanned": "",
            "public_contact_emails": "",
            "hr_recruiting_emails": "",
            "email_source_urls": "",
            "contact_page_urls": "",
            "contact_email_status": "",
            "contact_email_error": "",
        }
        if not seed_urls:
            row["contact_email_status"] = "No company or career URL available"
            return row, False

        queue = list(seed_urls)
        scanned: list[str] = []
        contact_pages: list[str] = []
        all_emails: dict[str, bool] = {}
        email_sources: set[str] = set()
        errors: list[str] = []
        while queue and len(scanned) < args.max_pages_per_company:
            page_url = queue.pop(0)
            if page_url in scanned:
                continue
            try:
                parser = ContactPageParser()
                parser.feed(fetch(page_url))
            except RuntimeError as exc:
                errors.append(f"{page_url}: {exc}")
                scanned.append(page_url)
                continue
            scanned.append(page_url)
            found_emails, links = _emails_from_page(page_url, parser)
            for email, hr_match in found_emails.items():
                all_emails[email] = all_emails.get(email, False) or hr_match
                email_sources.add(page_url)
            for link in links:
                if link not in queue and link not in scanned:
                    queue.append(link)
                if link not in contact_pages:
                    contact_pages.append(link)

        row["pages_scanned"] = " | ".join(scanned)
        row["contact_page_urls"] = " | ".join(contact_pages)
        row["public_contact_emails"] = " | ".join(all_emails)
        row["hr_recruiting_emails"] = " | ".join(
            email for email, is_hr in all_emails.items() if is_hr
        )
        row["email_source_urls"] = " | ".join(sorted(email_sources))
        if errors:
            row["contact_email_status"] = (
                "Fetch incomplete" if len(errors) < len(scanned) else "Fetch failed"
            )
            row["contact_email_error"] = " | ".join(errors)
        elif all_emails:
            row["contact_email_status"] = "Public email found"
        else:
            row["contact_email_status"] = "No public email found"
        return row, bool(errors)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    errors = 0
    with_emails = 0
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
                results = executor.map(process, enumerate(companies, start=1))
                for index, (row, had_error) in enumerate(results, start=1):
                    writer.writerow(row)
                    errors += had_error
                    with_emails += bool(row["public_contact_emails"])
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
        f"Wrote public contact email results for {len(companies)} companies to "
        f"{args.output} ({with_emails} with emails; {errors} page-fetch errors)."
    )
    return 1 if errors else 0


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
