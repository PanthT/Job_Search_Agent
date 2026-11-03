"""Find potential careers pages for members of Canadian defence associations."""

from __future__ import annotations

import argparse
import csv
import html
import http.client as http_client
import json
import logging
import os
import re
import ssl
import subprocess
import sys
import tempfile
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlparse, urlsplit, urlunsplit
from urllib.request import Request, urlopen
import requests
import certifi


CADSI_DIRECTORY_URL = (
    "https://www.defenceandsecurity.ca/en/membership/members-list"
)
AIAC_DIRECTORY_URL = "https://aiac.ca/membership/member-directory/"
REQUEST_TIMEOUT = 30
CAREER_TERMS = re.compile(
    r"\b(careers?|jobs?|join (?:our|the) team|work with us|"
    r"open positions|employment|job opportunities)\b",
    re.IGNORECASE,
)
DIRECT_POSTING_PATHS = re.compile(
    r"/(?:job|posting|viewjob|job-opening|job-detail)(?:/|$)",
    re.IGNORECASE,
)
JOB_LINK_PATHS = re.compile(
    r"(?:^|/)(?:jobs?|positions?|openings?|vacancies|"
    r"requisitions?|opportunities)(?:/|$)",
    re.IGNORECASE,
)
JOB_SKIP_PATHS = re.compile(
    r"(?:^|/)(?:careers?|jobs?|positions?|openings?|vacancies)/?$",
    re.IGNORECASE,
)
EXPERIENCE_PATTERN = re.compile(
    r"\b(?:(?:minimum|min\.?|at least|over|more than|"
    r"a minimum of)\s*)?(\d{1,2})\s*\+?\s*(?:to|-|–)\s*"
    r"(\d{1,2})\s*years?\s+(?:of\s+)?(?:relevant\s+)?experience\b|"
    r"\b(?:(?:minimum|min\.?|at least|over|more than|"
    r"a minimum of)\s*)?(\d{1,2})\s*\+?\s*years?"
    r"\s+(?:of\s+)?(?:relevant\s+)?experience\b",
    re.IGNORECASE,
)
SALARY_PATTERN = re.compile(
    r"(?P<currency>CAD|USD|C\$|US\$|\$)\s*\$?\s*"
    r"(?P<minimum>\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)"
    r"(?:\s*(?:-|–|to)\s*(?:CAD|USD|C\$|US\$|\$)?\s*"
    r"(?P<maximum>\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?))?"
    r"(?:\s*(?:/|per)?\s*(?P<unit>hour|hr|year|annual|month|week))?",
    re.IGNORECASE,
)
MAX_JOB_PAGES_PER_SITE = 20
MAX_DESCRIPTION_LENGTH = 20_000

# Hosts that aggregate postings for many employers. Jobs on these domains are
# not on the member's own website, so company-site-only filtering drops them.
# ATS hosts (greenhouse, lever, ashby, workday, etc.) are intentionally NOT
# listed here: a posting like boards.greenhouse.io/<company>/... is the
# member's own hiring pipeline, not a generic board.
JOB_BOARD_HOSTS = (
    "linkedin.com",
    "indeed.com",
    "glassdoor.com",
    "simplyhired.com",
    "ziprecruiter.com",
    "workopolis.com",
    "monster.com",
    "careerbuilder.com",
    "jobbank.gc.ca",
    "eluta.ca",
    "jobboom.com",
    "jobillico.com",
    "job-boards.greenhouse.io",
    "builtin.com",
    "jobera.com",
    "clearancejobs.com",
    "defencejobs.org",
    "facebook.com",
    "instagram.com",
    "x.com",
    "twitter.com",
    "youtube.com",
    "google.com",
    "bing.com",
    "duckduckgo.com",
)

# Company-website search should also skip the association directories.
COMPANY_SITE_SEARCH_EXCLUDED_HOSTS = JOB_BOARD_HOSTS + (
    "defenceandsecurity.ca",
    "aiac.ca",
)


def _hostname(url: str) -> str:
    """Return the lower-cased hostname without a leading www., or ''."""
    try:
        return (urlparse(url).hostname or "").casefold().removeprefix("www.")
    except Exception:
        return ""


def _is_job_board_host(host: str) -> bool:
    """True when *host* is (or is a subdomain of) a known job board."""
    host = (host or "").casefold().removeprefix("www.")
    if not host:
        return False
    return any(host == board or host.endswith(f".{board}") for board in JOB_BOARD_HOSTS)


def is_job_board_url(url: str) -> bool:
    """True when *url* points at a generic job board / aggregator."""
    return _is_job_board_host(_hostname(url))


def _registrable_domain(host: str) -> str:
    """Best-effort registrable domain (last two labels) for same-company checks."""
    host = (host or "").casefold().removeprefix("www.")
    parts = [part for part in host.split(".") if part]
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def is_company_site_url(url: str, company_website_url: str) -> bool:
    """True when *url* lives on the same registrable domain as the company site.

    Empty company site -> False. Job-board hosts never count, even if the
    strings happen to match.
    """
    host = _hostname(url)
    company_host = _hostname(company_website_url)
    if not host or not company_host:
        return False
    if _is_job_board_host(host):
        return False
    return _registrable_domain(host) == _registrable_domain(company_host)


# Company-specific hiring platforms: a posting like
# <company>.myworkdayjobs.com or jobs.ashbyhq.com/<company>/... belongs to
# that member's own pipeline, not a generic board, so company-site-only
# filtering keeps it.
ATS_HOSTS = (
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "myworkdayjobs.com",
    "workday.com",
    "smartrecruiters.com",
    "dayforcehcm.com",
    "rippling.com",
    "breezy.hr",
    "workable.com",
    "jazzhr.com",
    "zoho.com",
    "teamtailor.com",
    "pinpoint-applicant.com",
    "applicantstack.com",
    "applytojob.com",
    "careerplug.com",
)


def _is_ats_host(host: str) -> bool:
    host = (host or "").casefold().removeprefix("www.")
    if not host:
        return False
    return any(host == ats or host.endswith(f".{ats}") for ats in ATS_HOSTS)


def is_company_job_url(
    job_url: str, company_website_url: str, company_site_only: bool = True
) -> bool:
    """True when a crawled *job_url* counts as the member's own posting.

    Empty job URLs (careers-page-only rows) pass through as True so callers
    can keep them. Otherwise: job-board URLs are rejected, and when the
    company website is known the job must share its registrable domain or
    live on a company-specific ATS host.
    """
    if not job_url or not job_url.strip():
        return True
    if not company_site_only:
        return True
    if is_job_board_url(job_url):
        return False
    if not company_website_url or not company_website_url.strip():
        return True
    if is_company_site_url(job_url, company_website_url):
        return True
    return _is_ats_host(_hostname(job_url))


class DirectoryParser(HTMLParser):
    """Extract member names and pagination links from either directory page."""

    def __init__(self, directory: str) -> None:
        super().__init__(convert_charrefs=True)
        self.directory = directory
        self.members: list[str] = []
        self.next_page: str | None = None
        self._in_aiac_list = False
        self._aiac_list_depth = 0
        self._aiac_item_depth = 0
        self._aiac_name: list[str] = []
        self._in_cadsi_item = False
        self._cadsi_item_depth = 0
        self._cadsi_name_depth = 0
        self._cadsi_name: list[str] = []
        self._capture_cadsi_name = False

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())

        if (
            tag == "a"
            and attributes.get("fs-list-element") == "page-next"
            and attributes.get("href")
            and attributes["href"] != "#"
        ):
            self.next_page = attributes["href"]

        if self.directory == "aiac":
            if tag == "ul" and "member-list" in classes:
                self._in_aiac_list = True
                self._aiac_list_depth = 1
            elif self._in_aiac_list and tag == "ul":
                self._aiac_list_depth += 1
            elif self._in_aiac_list and tag == "li" and not self._aiac_item_depth:
                self._aiac_item_depth = 1
                self._aiac_name = []
            elif self._aiac_item_depth and tag == "li":
                self._aiac_item_depth += 1
        elif self.directory == "cadsi":
            if tag == "div" and "w-dyn-item" in classes and not self._in_cadsi_item:
                self._in_cadsi_item = True
                self._cadsi_item_depth = 1
                self._cadsi_name = []
            elif self._in_cadsi_item and tag == "div":
                self._cadsi_item_depth += 1

            if (
                self._in_cadsi_item
                and tag == "div"
                and {"body-small", "text-center"}.issubset(classes)
            ):
                self._capture_cadsi_name = True
                self._cadsi_name_depth = 1

    def handle_endtag(self, tag: str) -> None:
        if self.directory == "aiac":
            if self._aiac_item_depth and tag == "li":
                self._aiac_item_depth -= 1
                if not self._aiac_item_depth:
                    name = " ".join("".join(self._aiac_name).split())
                    if name:
                        self.members.append(name)
            elif self._in_aiac_list and tag == "ul":
                self._aiac_list_depth -= 1
                if self._aiac_list_depth == 0:
                    self._in_aiac_list = False
        elif self._in_cadsi_item and tag == "div":
            if self._capture_cadsi_name:
                self._cadsi_name_depth -= 1
                if self._cadsi_name_depth == 0:
                    self._capture_cadsi_name = False
                    name = " ".join("".join(self._cadsi_name).split())
                    if name:
                        self.members.append(name)
            self._cadsi_item_depth -= 1
            if self._cadsi_item_depth == 0:
                self._in_cadsi_item = False

    def handle_data(self, data: str) -> None:
        if self._aiac_item_depth:
            self._aiac_name.append(data)
        if self._capture_cadsi_name:
            self._cadsi_name.append(data)


class JobPageParser(HTMLParser):
    """Collect visible text, links, page headings, and JobPosting JSON-LD."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.links: list[dict[str, str]] = []
        self.jsonld: list[str] = []
        self.headings: list[str] = []
        self.metadata: list[str] = []
        self._script_type = ""
        self._script_content: list[str] = []
        self._in_title = False
        self._in_heading = False
        self._current_link = ""
        self._link_text: list[str] = []
        self._current_heading: list[str] = []
        self._skip_depth = 0  # inside script/style/noscript/template/head? no-head ok

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        if tag in {"script", "style", "noscript", "template"}:
            self._skip_depth += 1
        if tag == "script":
            self._script_type = attributes.get("type", "").casefold()
            self._script_content = []
        elif tag == "meta":
            meta_name = (
                attributes.get("property") or attributes.get("name") or ""
            ).casefold()
            if meta_name in {
                "og:title",
                "og:description",
                "og:site_name",
                "application-name",
                "description",
            }:
                content = attributes.get("content")
                if content:
                    self.metadata.append(content)
        elif tag == "img":
            alt = attributes.get("alt")
            if alt:
                self.metadata.append(alt)
        elif tag == "title":
            self._in_title = True
            self._current_heading = []
        elif tag in {"h1", "h2"}:
            self._in_heading = True
            self._current_heading = []
        elif tag == "a":
            self._current_link = attributes.get("href", "") or ""
            self._link_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "template"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "script":
            if self._script_type == "application/ld+json":
                self.jsonld.append("".join(self._script_content))
            self._script_type = ""
            self._script_content = []
        elif tag == "title":
            title = " ".join("".join(self._current_heading).split())
            if title:
                self.headings.append(title)
            self._in_title = False
        elif tag in {"h1", "h2"} and self._in_heading:
            heading = " ".join("".join(self._current_heading).split())
            if heading:
                self.headings.append(heading)
            self._in_heading = False
        elif tag == "a" and self._current_link:
            text = " ".join("".join(self._link_text).split())
            self.links.append({"href": self._current_link, "text": text})
            self._current_link = ""
            self._link_text = []

    def handle_data(self, data: str) -> None:
        if self._script_type == "application/ld+json":
            self._script_content.append(data)
            return
        if self._skip_depth:
            return
        stripped = data.strip()
        if not stripped:
            return
        # skip inline CSS blobs and JS fragments that leak as text
        if ("{" in stripped and "}" in stripped and
                (";" in stripped or ":" in stripped) and len(stripped.split()) < 60):
            return
        if stripped.startswith(("var ", "window.", "(function", "function ",
                                 "if(", "if (", "$(", "jQuery", "kendo.")):
            return
        self.text.append(data)
        if self._in_title or self._in_heading:
            self._current_heading.append(data)
        if self._current_link:
            self._link_text.append(data)


def _ascii_request_url(url: str) -> str:
    """Percent-encode non-ASCII URL components before passing them to urllib."""
    parts = urlsplit(url)
    host = parts.hostname
    if not host:
        raise ValueError(f"Invalid URL with no hostname: {url!r}")

    ascii_host = host.encode("idna").decode("ascii")
    if ":" in ascii_host and not ascii_host.startswith("["):
        ascii_host = f"[{ascii_host}]"
    userinfo = (
        parts.netloc.rsplit("@", 1)[0] if "@" in parts.netloc else ""
    )
    netloc = f"{userinfo}@{ascii_host}" if userinfo else ascii_host
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"

    return urlunsplit(
        (
            parts.scheme,
            netloc,
            quote(parts.path, safe="/%:@!$&'()*+,;=-._~"),
            quote(parts.query, safe="/?%:@!$&'()*+,;=-._~"),
            quote(parts.fragment, safe="/?%:@!$&'()*+,;=-._~"),
        )
    )


def _fetch_page(url: str) -> str:
    request_url = _ascii_request_url(url)
    request = Request(
        request_url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; MemberCareerSearch/1.0)"},
    )
    try:
        context = ssl.create_default_context(cafile=certifi.where())
        with urlopen(
            request, timeout=REQUEST_TIMEOUT, context=context
        ) as response:
            try:
                raw = response.read()
            except http_client.IncompleteRead as exc:
                # Server closed a chunked connection early (common on large
                # ATS / career pages). Keep the bytes we received rather
                # than crashing the run; parsers tolerate truncation.
                raw = exc.partial or b""
                if not raw:
                    raise RuntimeError(
                        f"Could not fetch directory page {url}: truncated response"
                    ) from exc
            return raw.decode("utf-8", errors="replace")
    except HTTPError as exc:
        raise RuntimeError(f"Could not fetch directory page {url}: HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as python_error:
        try:
            response = subprocess.run(
                [
                    "curl",
                    "--fail",
                    "--location",
                    "--silent",
                    "--show-error",
                    "--max-time",
                    str(REQUEST_TIMEOUT),
                    "--user-agent",
                    "Mozilla/5.0 (compatible; MemberCareerSearch/1.0)",
                    request_url,
                ],
                check=False,
                capture_output=True,
                timeout=REQUEST_TIMEOUT + 5,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"Could not fetch directory page {url} with Python "
                f"({python_error}); curl is not installed."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Could not fetch directory page {url}: Python request failed "
                f"({python_error}) and curl timed out after {REQUEST_TIMEOUT} seconds."
            ) from exc

        if response.returncode:
            detail = response.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"Could not fetch directory page {url}: Python request failed "
                f"({python_error}); curl failed"
                f"{f': {detail}' if detail else '.'}"
            )
        return response.stdout.decode("utf-8", errors="replace")


def _plain_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = re.sub(r"<[^>]+>", " ", value)
    return " ".join(html.unescape(value).split())


def _iter_job_postings(value: Any) -> list[dict[str, Any]]:
    postings: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            postings.extend(_iter_job_postings(item))
    elif isinstance(value, dict):
        job_type = value.get("@type", [])
        if isinstance(job_type, str):
            job_type = [job_type]
        if isinstance(job_type, list) and "JobPosting" in job_type:
            postings.append(value)
        for key, item in value.items():
            if key not in {"@context", "@type"}:
                postings.extend(_iter_job_postings(item))
    return postings


def _job_postings_from_page(parser: JobPageParser) -> list[dict[str, Any]]:
    postings: list[dict[str, Any]] = []
    for block in parser.jsonld:
        try:
            structured_data = json.loads(block)
        except json.JSONDecodeError:
            continue
        postings.extend(_iter_job_postings(structured_data))
    return postings


def _salary_from_structured_data(salary: Any) -> str:
    if not isinstance(salary, dict):
        return _plain_text(salary)
    currency = str(salary.get("currency") or "").strip()
    value = salary.get("value", salary)
    if not isinstance(value, dict):
        value = {"value": value}
    minimum = value.get("minValue", value.get("value"))
    maximum = value.get("maxValue")
    if minimum is None:
        return ""
    try:
        minimum_text = f"{float(minimum):,.0f}"
        maximum_text = (
            f"{float(maximum):,.0f}" if maximum is not None else ""
        )
    except (TypeError, ValueError):
        return ""
    amount = (
        f"{minimum_text}-{maximum_text}" if maximum_text else minimum_text
    )
    unit = _plain_text(value.get("unitText", "")).casefold()
    if unit in {"year", "yearly", "annual", "per year"}:
        unit = "per year"
    elif unit in {"hour", "hourly", "per hour"}:
        unit = "per hour"
    elif unit in {"month", "monthly", "per month"}:
        unit = "per month"
    elif unit in {"week", "weekly", "per week"}:
        unit = "per week"
    return " ".join(part for part in (currency, amount, unit) if part)


def _salary_from_text(text: str) -> str:
    match = SALARY_PATTERN.search(text)
    if not match:
        return ""
    currency = match.group("currency").upper()
    minimum = match.group("minimum")
    maximum = match.group("maximum")
    amount = f"{minimum}-{maximum}" if maximum else minimum
    unit = f"per {match.group('unit')}" if match.group("unit") else ""
    return " ".join(part for part in (currency, amount, unit or "") if part)


def _experience_from_posting(posting: dict[str, Any], text: str) -> str:
    requirement = posting.get("experienceRequirements")
    if isinstance(requirement, dict):
        months = requirement.get("monthsOfExperience")
        try:
            if months is not None:
                years = float(months) / 12
                return f"{years:g} years"
        except (TypeError, ValueError):
            pass
    requirement_text = _plain_text(requirement)
    match = EXPERIENCE_PATTERN.search(requirement_text or text)
    if not match:
        return ""
    if match.group(1):
        return f"{match.group(1)}-{match.group(2)} years"
    return f"{match.group(3)}+ years" if "+" in match.group(0) else f"{match.group(3)} years"


def _job_row(
    association: str,
    member: str,
    career_page_url: str,
    job_url: str,
    posting: dict[str, Any],
    parser: JobPageParser,
    fallback_title: str = "",
) -> dict[str, str]:
    description = _plain_text(posting.get("description"))
    if not description:
        description = " ".join(" ".join(parser.text).split())
    salary = _salary_from_structured_data(posting.get("baseSalary"))
    if not salary:
        salary = _salary_from_text(description)
    return {
        "association": association,
        "member": member,
        "job_title": _plain_text(posting.get("title"))
        or fallback_title
        or next(
            (
                heading
                for heading in parser.headings
                if "career" not in heading.casefold()
                and "job search" not in heading.casefold()
            ),
            "",
        ),
        "description": description[:MAX_DESCRIPTION_LENGTH],
        "years_of_experience": _experience_from_posting(posting, description),
        "salary": salary,
        "job_url": job_url,
        "careers_page_url": career_page_url,
        "status": "Found",
        "company_verification": "Not checked",
        "career_page_company_verification": "Not checked",
    }


def _job_posting_url(posting: dict[str, Any], fallback_url: str) -> str:
    url = posting.get("url")
    if isinstance(url, dict):
        url = url.get("@id")
    return url if isinstance(url, str) and url.strip() else fallback_url


def _normalize_company_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return " ".join(re.findall(r"[a-z0-9]+", normalized.casefold()))


def _company_name_variants(company: str) -> list[str]:
    aliases = [company]
    aliases.extend(re.findall(r"\(([^)]+)\)", company))
    aliases.extend(part for part in re.split(r"[()]", company) if part != company)
    legal_suffixes = {
        "inc",
        "incorporated",
        "corp",
        "corporation",
        "company",
        "co",
        "ltd",
        "limited",
        "llc",
        "llp",
        "lp",
        "plc",
        "ulc",
        "ltee",
        "sarl",
        "sa",
        "gmbh",
    }
    variants: list[str] = []
    for alias in aliases:
        words = _normalize_company_name(alias).split()
        while words and words[-1] in legal_suffixes:
            words.pop()
        normalized = " ".join(words)
        if normalized and normalized not in variants:
            variants.append(normalized)
    return variants


def _searchable_company_name(company: str) -> str:
    decomposed = unicodedata.normalize("NFKD", company)
    ascii_name = "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    ).encode("ascii", errors="ignore").decode("ascii")
    return ascii_name or company.encode("ascii", errors="ignore").decode("ascii")


def _safe_print(message: str, stream: Any) -> None:
    try:
        print(message, file=stream, flush=True)
    except UnicodeEncodeError:
        encoding = stream.encoding or "ascii"
        escaped_message = message.encode(
            encoding, errors="backslashreplace"
        ).decode(encoding)
        stream.write(f"{escaped_message}\n")
        stream.flush()


class _EncodingSafeTextStream:
    def __init__(self, stream: Any) -> None:
        self._stream = stream

    def write(self, text: str) -> int:
        try:
            return self._stream.write(text)
        except UnicodeEncodeError:
            encoding = getattr(self._stream, "encoding", None) or "ascii"
            escaped_text = text.encode(
                encoding, errors="backslashreplace"
            ).decode(encoding)
            return self._stream.write(escaped_text)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


def _configure_console_encoding() -> None:
    streams = {
        "stdout": sys.stdout,
        "stderr": sys.stderr,
        "__stdout__": sys.__stdout__,
        "__stderr__": sys.__stderr__,
    }
    safe_streams: dict[int, _EncodingSafeTextStream] = {}
    for stream in streams.values():
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(errors="backslashreplace")
        if id(stream) not in safe_streams:
            safe_streams[id(stream)] = (
                stream
                if isinstance(stream, _EncodingSafeTextStream)
                else _EncodingSafeTextStream(stream)
            )

    sys.stdout = safe_streams[id(streams["stdout"])]
    sys.stderr = safe_streams[id(streams["stderr"])]
    if streams["__stdout__"] is not None:
        sys.__stdout__ = safe_streams[id(streams["__stdout__"])]
    if streams["__stderr__"] is not None:
        sys.__stderr__ = safe_streams[id(streams["__stderr__"])]

    loggers = [logging.getLogger()]
    loggers.extend(
        logger
        for logger in logging.root.manager.loggerDict.values()
        if isinstance(logger, logging.Logger)
    )
    for logger in loggers:
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler):
                replacement = safe_streams.get(id(handler.stream))
                if replacement is not None and handler.stream is not replacement:
                    handler.setStream(replacement)


def verify_company_on_page(
    company: str,
    parser: JobPageParser,
    url: str,
    organization: Any = None,
) -> str:
    """Check whether a linked page identifies itself as the requested company."""
    organization_name = ""
    if isinstance(organization, dict):
        organization_name = _plain_text(organization.get("name"))
    elif isinstance(organization, str):
        organization_name = _plain_text(organization)
    if organization_name and _company_names_match(company, organization_name):
        return "Found in structured organization data"

    page_content = " ".join(
        parser.text
        + parser.headings
        + parser.metadata
        + parser.jsonld
    )
    if _company_names_match(company, page_content):
        return "Found in page content"

    host = (urlparse(url).hostname or "").removeprefix("www.")
    if _company_names_match(company, host.replace(".", " ")):
        return "Found in domain"
    return "Not found"


def _company_names_match(company: str, content: str) -> bool:
    normalized_content = _normalize_company_name(content)
    content_words = set(normalized_content.split())
    for variant in _company_name_variants(company):
        if re.search(
            rf"(?<![a-z0-9]){re.escape(variant)}(?![a-z0-9])",
            normalized_content,
        ):
            return True
        significant_words = variant.split()
        if len(significant_words) > 1 and all(
            word in content_words for word in significant_words
        ):
            return True
    return False


def _host_matches_company(host: str, company: str) -> bool:
    domain_label = (host or "").casefold().removeprefix("www.").split(".", 1)[0]
    domain_label = _normalize_company_name(domain_label).replace(" ", "")
    return any(
        domain_label == variant.replace(" ", "")
        for variant in _company_name_variants(company)
    )


def _is_job_link(link: dict[str, str], base_url: str) -> bool:
    url = urljoin(base_url, link["href"])
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    if parsed._replace(fragment="").geturl() == urlparse(base_url)._replace(
        fragment=""
    ).geturl():
        return False
    if is_job_board_url(url):
        # Never follow links to generic job boards / aggregators; we only
        # want postings on the member's own website (or its ATS pipeline).
        return False
    path = parsed.path.rstrip("/") or "/"
    if JOB_SKIP_PATHS.search(parsed.path):
        return False
    host = (parsed.hostname or "").casefold()
    known_job_hosts = (
        "jobs.lever.co",
        "boards.greenhouse.io",
        "jobs.ashbyhq.com",
        "jobs.smartrecruiters.com",
    )
    return bool(
        JOB_LINK_PATHS.search(path)
        or any(
            host == known_host or host.endswith(f".{known_host}")
            for known_host in known_job_hosts
        )
        or re.search(r"\bapply\b", link["text"], re.IGNORECASE)
    )


def crawl_career_page(
    association: str,
    member: str,
    career_page_url: str,
    max_job_pages: int = MAX_JOB_PAGES_PER_SITE,
    delay: float = 0.25,
) -> list[dict[str, str]]:
    """Visit a careers page and a bounded number of its likely job links."""
    page = JobPageParser()
    page.feed(_fetch_page(career_page_url))

    structured_postings = _job_postings_from_page(page)
    career_page_company_verification = verify_company_on_page(
        member, page, career_page_url
    )
    if structured_postings:
        rows = [
            _job_row(
                association,
                member,
                career_page_url,
                _job_posting_url(posting, career_page_url),
                posting,
                page,
            )
            for posting in structured_postings[:max_job_pages]
        ]
        for row, posting in zip(rows, structured_postings):
            row["career_page_company_verification"] = (
                career_page_company_verification
            )
            row["company_verification"] = verify_company_on_page(
                member,
                page,
                row["job_url"],
                posting.get("hiringOrganization"),
            )
        return rows

    job_links: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for link in page.links:
        if not _is_job_link(link, career_page_url):
            continue
        url = urljoin(career_page_url, link["href"]).split("#", 1)[0]
        if url not in seen_urls:
            seen_urls.add(url)
            job_links.append({"url": url, "text": link["text"]})
        if len(job_links) >= max_job_pages:
            break

    rows: list[dict[str, str]] = []
    for index, job_link in enumerate(job_links):
        if index and delay:
            time.sleep(delay)
        job_url = job_link["url"]
        try:
            job_page = JobPageParser()
            job_page.feed(_fetch_page(job_url))
        except RuntimeError as exc:
            rows.append(
                {
                    "association": association,
                    "member": member,
                    "job_title": job_link["text"],
                    "description": "",
                    "years_of_experience": "",
                    "salary": "",
                    "job_url": job_url,
                    "careers_page_url": career_page_url,
                    "status": str(exc),
                    "company_verification": "Could not verify",
                    "career_page_company_verification": career_page_company_verification,
                }
            )
            continue

        postings = _job_postings_from_page(job_page)
        job_page_company_verification = verify_company_on_page(
            member,
            job_page,
            job_url,
        )
        if postings:
            posting_rows = [
                _job_row(
                    association,
                    member,
                    career_page_url,
                    _job_posting_url(posting, job_url),
                    posting,
                    job_page,
                    job_link["text"],
                )
                for posting in postings
            ]
            for row, posting in zip(posting_rows, postings):
                row["career_page_company_verification"] = (
                    career_page_company_verification
                )
                row["company_verification"] = verify_company_on_page(
                    member,
                    job_page,
                    row["job_url"],
                    posting.get("hiringOrganization"),
                )
            rows.extend(posting_rows)
        else:
            row = _job_row(
                    association,
                    member,
                    career_page_url,
                    job_url,
                    {},
                    job_page,
                    job_link["text"],
                )
            row["career_page_company_verification"] = (
                career_page_company_verification
            )
            row["company_verification"] = job_page_company_verification
            rows.append(row)

    if not rows:
        rows.append(
            {
                "association": association,
                "member": member,
                "job_title": "",
                "description": "",
                "years_of_experience": "",
                "salary": "",
                "job_url": "",
                "careers_page_url": career_page_url,
                "status": "No job postings found on this page",
                "company_verification": career_page_company_verification,
                "career_page_company_verification": career_page_company_verification,
            }
        )
    return rows


def get_members(directory: str, page_url: str) -> list[str]:
    """Fetch every member listed by AIAC or CADSI."""
    members: list[str] = []
    seen_pages: set[str] = set()
    current_url = page_url

    while current_url and current_url not in seen_pages:
        seen_pages.add(current_url)
        parser = DirectoryParser(directory)
        parser.feed(_fetch_page(current_url))
        members.extend(parser.members)
        current_url = (
            urljoin(current_url, parser.next_page)
            if parser.next_page
            else ""
        )
        if current_url and len(seen_pages) >= 100:
            raise RuntimeError(
                f"Stopped reading {directory} after 100 directory pages; "
                "pagination may have changed."
            )

    unique_members = list(dict.fromkeys(members))
    if not unique_members:
        raise RuntimeError(
            f"No {directory.upper()} members were found. "
            "The directory page structure may have changed."
        )
    return unique_members


def _create_web_search() -> Any:
    try:
        from langchain_community.utilities import DuckDuckGoSearchAPIWrapper
    except ImportError as exc:
        raise RuntimeError(
            "The LangChain DuckDuckGo integration is missing. "
            "Install project dependencies with 'pip install -r requirements.txt'."
        ) from exc
    return DuckDuckGoSearchAPIWrapper(
        backend="duckduckgo,bing,brave,google,mojeek,yahoo"
    )


def _create_company_website_search() -> Any:
    try:
        from langchain_community.utilities import DuckDuckGoSearchAPIWrapper
    except ImportError as exc:
        raise RuntimeError(
            "The LangChain DuckDuckGo integration is missing. "
            "Install project dependencies with 'pip install -r requirements.txt'."
        ) from exc
    return DuckDuckGoSearchAPIWrapper(
        backend="duckduckgo,bing,brave,google,mojeek,yahoo"
    )


def find_career_pages(
    search: Any,
    company: str,
    company_website_url: str = "",
    company_site_only: bool = True,
) -> list[dict[str, str]]:
    """Return search results that appear to be an organization's careers page.

    When *company_site_only* is True, generic job-board / aggregator URLs are
    dropped. When *company_website_url* is known, results are additionally
    required to live on the same registrable domain as the company site
    (company-owned ATS subdomains such as jobs.company.com still pass).
    """
    query = f'"{_searchable_company_name(company)}" careers jobs official'
    try:
        results = search.results(query, max_results=8)
    except Exception as exc:
        if str(exc).strip().casefold().rstrip(".") == "no results found":
            return []
        raise RuntimeError(
            f"Web search failed for {company!r}. Check network/search service "
            f"availability. Details: {exc}"
        ) from exc
    if not isinstance(results, list):
        raise RuntimeError(
            f"Web search returned an unexpected response for {company!r}."
        )

    matches: list[dict[str, str]] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        title = result.get("title")
        url = result.get("link")
        snippet = result.get("snippet") or ""
        if not isinstance(title, str) or not isinstance(url, str):
            continue
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        if company_site_only and is_job_board_url(url):
            continue
        if company_website_url and not is_company_site_url(url, company_website_url):
            continue
        text = f"{title} {snippet} {parsed.path}"
        if not CAREER_TERMS.search(text):
            continue
        if DIRECT_POSTING_PATHS.search(parsed.path):
            continue
        matches.append(
            {
                "url": url,
                "title": title.strip(),
                "snippet": str(snippet).strip(),
            }
        )
        if len(matches) == 3:
            break
    return matches


def find_company_linkedin(search: Any, company: str) -> dict[str, str]:
    """Find a LinkedIn company page only when result details identify the member."""
    query = (
        f'site:linkedin.com/company "{_searchable_company_name(company)}"'
    )
    try:
        results = search.results(query, max_results=8)
    except Exception as exc:
        if str(exc).strip().casefold().rstrip(".") == "no results found":
            return {"url": "", "verification": "No matching page found"}
        raise RuntimeError(
            f"LinkedIn search failed for {company!r}. Check network/search "
            f"service availability. Details: {exc}"
        ) from exc
    if not isinstance(results, list):
        raise RuntimeError(
            f"LinkedIn search returned an unexpected response for {company!r}."
        )

    for result in results:
        if not isinstance(result, dict):
            continue
        url = result.get("link")
        title = result.get("title")
        snippet = result.get("snippet") or ""
        if not isinstance(url, str) or not isinstance(title, str):
            continue
        parsed = urlparse(url)
        host = (parsed.hostname or "").casefold().removeprefix("www.")
        if host != "linkedin.com" or not re.match(
            r"^/(?:company|showcase)/[^/]+/?$", parsed.path, re.IGNORECASE
        ):
            continue
        identifying_text = " ".join(
            (
                title,
                str(snippet),
                parsed.path.rsplit("/", 2)[-2].replace("-", " "),
            )
        )
        if _company_names_match(company, identifying_text):
            return {
                "url": url,
                "verification": "Name matched in LinkedIn search result",
            }
    return {"url": "", "verification": "No matching page found"}


def find_company_website(search: Any, company: str) -> dict[str, str]:
    """Find a company website and check whether the page identifies the member."""
    excluded_hosts = COMPANY_SITE_SEARCH_EXCLUDED_HOSTS
    name = _searchable_company_name(company)
    name_variants = _company_name_variants(name)
    fallback_name = next(
        (variant for variant in name_variants if variant.casefold() != name.casefold()),
        "",
    )
    queries = list(
        dict.fromkeys(
            f'"{variant}" official company website'
            for variant in (name, fallback_name)
            if variant
        )
    )
    unverified_match: dict[str, str] | None = None
    search_errors: list[str] = []
    completed_search = False

    for query in queries:
        try:
            results = search.results(query, max_results=8)
        except Exception as exc:
            if str(exc).strip().casefold().rstrip(".") == "no results found":
                completed_search = True
                continue
            search_errors.append(str(exc))
            continue
        if not isinstance(results, list):
            search_errors.append("Search returned an unexpected response.")
            continue
        completed_search = True

        candidates: list[tuple[str, str, bool, bool]] = []
        seen_urls: set[str] = set()
        for result in results:
            if not isinstance(result, dict):
                continue
            url = result.get("link")
            title = result.get("title")
            snippet = result.get("snippet") or ""
            if not isinstance(url, str) or not isinstance(title, str):
                continue
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                continue
            host = parsed.hostname.casefold().removeprefix("www.")
            if any(
                host == excluded or host.endswith(f".{excluded}")
                for excluded in excluded_hosts
            ):
                continue
            homepage_url = f"{parsed.scheme}://{parsed.netloc}/"
            if homepage_url in seen_urls:
                continue
            seen_urls.add(homepage_url)
            identifying_text = " ".join(
                (title, str(snippet), host.replace(".", " "))
            )
            matched_result = _company_names_match(company, identifying_text)
            domain_match = _host_matches_company(host, company)
            official_result = matched_result and bool(
                re.search(r"\b(official|homepage|home page|company website)\b",
                          title, re.IGNORECASE)
            )
            candidates.append(
                (url, homepage_url, domain_match, official_result)
            )

        candidates.sort(
            key=lambda candidate: (not candidate[2], not candidate[3])
        )
        for url, homepage_url, domain_match, official_result in candidates[:5]:
            try:
                page = JobPageParser()
                page.feed(_fetch_page(url))
            except RuntimeError:
                if (domain_match or official_result) and unverified_match is None:
                    unverified_match = {
                        "url": homepage_url,
                        "verification": (
                            "Name matched in search result; site not verified"
                        ),
                    }
                continue
            verification = verify_company_on_page(company, page, url)
            if verification != "Not found" and (domain_match or official_result):
                return {"url": homepage_url, "verification": verification}

    if not completed_search and search_errors:
        search_detail = "; ".join(search_errors)
    else:
        search_detail = ""
    if search_detail:
        _safe_print(
            f"Company website search had no usable results for {company!r}; "
            "checking common domains for compact company names.",
            sys.stderr,
        )
    for variant in name_variants:
        if " " in variant or not re.search(r"[a-z]", variant):
            continue
        suffixes = (".ai", ".com", ".ca", ".io") if re.search(
            r"\d", variant
        ) else (".com", ".ca", ".ai", ".io")
        for suffix in suffixes:
            url = f"https://www.{variant}{suffix}/"
            try:
                page = JobPageParser()
                page.feed(_fetch_page(url))
            except RuntimeError:
                continue
            verification = verify_company_on_page(company, page, url)
            if verification in {
                "Found in structured organization data",
                "Found in page content",
            }:
                return {"url": url, "verification": verification}

    if not completed_search and search_errors:
        raise RuntimeError(
            f"Company website search failed for {company!r}. Check network/search "
            f"service availability. Details: {'; '.join(search_errors)}"
        )
    if unverified_match:
        return unverified_match
    return {"url": "", "verification": "No matching website found"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Find potential careers pages for members listed by CADSI and AIAC. "
            "Results are web-search leads, not verified vacancies."
        )
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("member_careers.csv"),
        help="CSV output path (default: member_careers.csv)",
    )
    parser.add_argument(
        "--jobs-output",
        type=Path,
        default=Path("member_jobs.csv"),
        help="CSV output path for job details (default: member_jobs.csv)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help=(
            "Search at most this many members per directory; 0 searches all "
            "(default: 0)."
        ),
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.25,
        help="Seconds to wait between searches (default: 0.25).",
    )
    parser.add_argument(
        "--max-job-pages",
        type=int,
        default=MAX_JOB_PAGES_PER_SITE,
        help=(
            "Maximum job links to visit from each careers page "
            f"(default: {MAX_JOB_PAGES_PER_SITE})."
        ),
    )
    parser.add_argument(
        "--company-site-only",
        dest="company_site_only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Keep only careers pages and job links on the member's own "
            "website (drops generic job boards such as LinkedIn, Indeed, "
            "Glassdoor; default: enabled). Use --no-company-site-only to "
            "restore the old behaviour."
        ),
    )
    parser.add_argument(
        "--websites-only",
        action="store_true",
        help=(
            "Write member_careers.csv with every CADSI and AIAC member and "
            "search for company websites with DuckDuckGo first and alternate "
            "backends as fallback; do not search careers pages or modify the "
            "jobs CSV."
        ),
    )
    return parser


def _write_member_websites(
    args: argparse.Namespace,
    member_groups: list[tuple[str, str, list[str]]],
    fieldnames: list[str],
) -> int:
    website_search = _create_company_website_search()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    errors = 0
    found = 0
    processed_associations = {association for association, _, _ in member_groups}
    existing_by_member: dict[tuple[str, str], dict[str, str]] = {}
    preserved_rows: list[dict[str, str]] = []
    if args.output.exists():
        with args.output.open(encoding="utf-8-sig", newline="") as source_csv:
            existing_reader = csv.DictReader(source_csv)
            existing_fieldnames = existing_reader.fieldnames or []
            fieldnames = list(dict.fromkeys(fieldnames + existing_fieldnames))
            for existing_row in existing_reader:
                key = (
                    existing_row.get("association", ""),
                    existing_row.get("member", ""),
                )
                if key[0] in processed_associations:
                    existing_by_member[key] = existing_row
                else:
                    preserved_rows.append(existing_row)

    temporary_path: Path | None = None

    def lookup(
        item: tuple[str, str],
    ) -> tuple[dict[str, str], str]:
        association, member = item
        existing_row = existing_by_member.get((association, member), {})
        existing_website = (
            existing_row.get("member_website")
            or existing_row.get("directory_url")
            or ""
        ).strip()
        if (
            existing_website
            and existing_row.get("company_website_verification")
            in {"Found in page content", "Found in domain"}
        ):
            return {
                "url": existing_website,
                "verification": existing_row[
                    "company_website_verification"
                ],
            }, ""
        try:
            return find_company_website(website_search, member), ""
        except RuntimeError as exc:
            return {"url": "", "verification": "Search failed"}, str(exc)

    try:
        with tempfile.NamedTemporaryFile(
            "w",
            newline="",
            encoding="utf-8-sig",
            dir=args.output.parent,
            prefix=f".{args.output.name}.",
            suffix=".tmp",
            delete=False,
        ) as career_csv:
            temporary_path = Path(career_csv.name)
            career_writer = csv.DictWriter(career_csv, fieldnames=fieldnames)
            career_writer.writeheader()
            with ThreadPoolExecutor(max_workers=8) as executor:
                for association, page_url, members in member_groups:
                    results = executor.map(
                        lookup, ((association, member) for member in members)
                    )
                    for index, (member, result) in enumerate(
                        zip(members, results), start=1
                    ):
                        company_website, search_error = result
                        if search_error:
                            errors += 1
                        existing_row = existing_by_member.get(
                            (association, member), {}
                        )
                        website_url = (
                            company_website["url"]
                            or existing_row.get("member_website", "")
                            or existing_row.get("directory_url", "")
                        )
                        website_verification = (
                            company_website["verification"]
                            if company_website["url"]
                            else existing_row.get(
                                "company_website_verification",
                                company_website["verification"],
                            )
                        )
                        if website_url:
                            found += 1
                        row = {
                            "potential_careers_page": "",
                            "search_result_title": "",
                            "search_snippet": "",
                            "company_verification": "Not checked",
                            "linkedin_company_page": "",
                            "linkedin_verification": "",
                            **existing_row,
                        }
                        row.update(
                            {
                                "association": association,
                                "member": member,
                                "association_directory_url": page_url,
                                "member_website": website_url,
                                "directory_url": website_url,
                                "company_website_verification": (
                                    website_verification
                                ),
                                "search_error": search_error,
                            }
                        )
                        career_writer.writerow(row)
                        career_csv.flush()
                        if index % 25 == 0 or index == len(members):
                            _safe_print(
                                f"{association} websites: "
                                f"{index}/{len(members)}",
                                sys.stderr,
                            )
            for row in preserved_rows:
                career_writer.writerow(row)
        assert temporary_path is not None
        os.replace(temporary_path, args.output)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()

    total = sum(len(members) for _, _, members in member_groups)
    associations = " and ".join(
        dict.fromkeys(association for association, _, _ in member_groups)
    )
    _safe_print(
        f"Wrote {total} {associations} member websites to {args.output} "
        f"({found} websites found, {errors} search errors).",
        sys.stdout,
    )
    return 1 if errors else 0


def run(args: argparse.Namespace) -> int:
    _configure_console_encoding()
    if args.limit < 0:
        raise ValueError("--limit must be zero or a positive number")
    if args.delay < 0:
        raise ValueError("--delay must be zero or a positive number")
    if args.max_job_pages <= 0:
        raise ValueError("--max-job-pages must be a positive number")
    if args.output.resolve() == args.jobs_output.resolve():
        raise ValueError("--output and --jobs-output must be different files")

    websites_only = getattr(args, "websites_only", False)
    directories = (
        ("CADSI", "cadsi", CADSI_DIRECTORY_URL),
        ("AIAC", "aiac", AIAC_DIRECTORY_URL),
    )
    member_groups: list[tuple[str, str, list[str]]] = []
    for association, directory, page_url in directories:
        members = get_members(directory, page_url)
        if args.limit:
            members = members[: args.limit]
        member_groups.append((association, page_url, members))

    fieldnames = [
        "association",
        "member",
        "association_directory_url",
        "member_website",
        "directory_url",
        "company_website_verification",
        "potential_careers_page",
        "search_result_title",
        "search_snippet",
        "company_verification",
        "linkedin_company_page",
        "linkedin_verification",
        "search_error",
    ]
    if websites_only:
        return _write_member_websites(args, member_groups, fieldnames)

    search = _create_web_search()
    website_search = _create_company_website_search()
    job_fieldnames = [
        "association",
        "member",
        "job_title",
        "description",
        "years_of_experience",
        "salary",
        "job_url",
        "careers_page_url",
        "company_verification",
        "career_page_company_verification",
        "member_website",
        "directory_url",
        "company_website_verification",
        "linkedin_company_page",
        "linkedin_verification",
        "status",
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.jobs_output.parent.mkdir(parents=True, exist_ok=True)
    searched = 0
    errors = 0

    with (
        args.output.open("w", newline="", encoding="utf-8-sig") as career_csv,
        args.jobs_output.open("w", newline="", encoding="utf-8-sig") as jobs_csv,
    ):
        career_writer = csv.DictWriter(career_csv, fieldnames=fieldnames)
        job_writer = csv.DictWriter(jobs_csv, fieldnames=job_fieldnames)
        career_writer.writeheader()
        job_writer.writeheader()

        for association, page_url, members in member_groups:
            _safe_print(
                f"{association}: searching {len(members)} member companies",
                sys.stdout,
            )

            for index, member in enumerate(members, start=1):
                searched += 1
                search_error = ""
                company_site_only = getattr(args, "company_site_only", True)
                try:
                    linkedin = find_company_linkedin(search, member)
                except RuntimeError as exc:
                    linkedin = {
                        "url": "",
                        "verification": "Search failed",
                    }
                    search_error = str(exc)
                    errors += 1
                try:
                    company_website = find_company_website(
                        website_search, member
                    )
                except RuntimeError as exc:
                    company_website = {
                        "url": "",
                        "verification": "Search failed",
                    }
                    search_error = (
                        f"{search_error}; {exc}" if search_error else str(exc)
                    )
                    errors += 1
                # Find careers pages. Default (company_site_only=True) drops
                # generic job boards; when the company website is known the
                # careers page must also share its registrable domain.
                try:
                    career_pages = find_career_pages(
                        search,
                        member,
                        company_website_url=company_website["url"],
                        company_site_only=company_site_only,
                    )
                except RuntimeError as exc:
                    career_pages = []
                    search_error = (
                        f"{search_error}; {exc}" if search_error else str(exc)
                    )
                    errors += 1

                if career_pages:
                    for career_page in career_pages:
                        career_page_url = career_page["url"]
                        try:
                            job_rows = crawl_career_page(
                                association,
                                member,
                                career_page_url,
                                args.max_job_pages,
                                args.delay,
                            )
                        except RuntimeError as exc:
                            job_rows = [
                                {
                                    "association": association,
                                    "member": member,
                                    "job_title": "",
                                    "description": "",
                                    "years_of_experience": "",
                                    "salary": "",
                                    "job_url": "",
                                    "careers_page_url": career_page_url,
                                    "status": str(exc),
                                    "company_verification": "Could not verify",
                                    "career_page_company_verification": "Could not verify",
                                }
                            ]
                        for job_row in job_rows:
                            job_row["linkedin_company_page"] = linkedin["url"]
                            job_row["linkedin_verification"] = linkedin[
                                "verification"
                            ]
                            job_row["member_website"] = company_website["url"]
                            job_row["directory_url"] = company_website["url"]
                            job_row["company_website_verification"] = (
                                company_website["verification"]
                            )
                            if company_site_only and job_row.get("job_url"):
                                if not is_company_job_url(
                                    job_row["job_url"], company_website["url"], True
                                ):
                                    job_row["status"] = (
                                        "Filtered: job board link, not on "
                                        "company website"
                                    )
                                    job_row["company_verification"] = (
                                        "Filtered: job board link"
                                    )
                            job_writer.writerow(job_row)
                            if job_row["status"] not in {
                                "Found",
                                "No job postings found on this page",
                            }:
                                errors += 1
                        career_writer.writerow(
                            {
                                "association": association,
                                "member": member,
                                "association_directory_url": page_url,
                                "member_website": company_website["url"],
                                "directory_url": company_website["url"],
                                "company_website_verification": (
                                    company_website["verification"]
                                ),
                                "potential_careers_page": career_page_url,
                                "search_result_title": career_page["title"],
                                "search_snippet": career_page["snippet"],
                                "company_verification": (
                                    job_rows[0].get(
                                        "career_page_company_verification",
                                        "Could not verify",
                                    )
                                    if job_rows
                                    else "Could not verify"
                                ),
                                "linkedin_company_page": linkedin["url"],
                                "linkedin_verification": linkedin["verification"],
                                "search_error": search_error,
                            }
                        )
                        career_csv.flush()
                        jobs_csv.flush()
                else:
                    career_writer.writerow(
                        {
                            "association": association,
                            "member": member,
                            "association_directory_url": page_url,
                            "member_website": company_website["url"],
                            "directory_url": company_website["url"],
                            "company_website_verification": (
                                company_website["verification"]
                            ),
                            "potential_careers_page": "",
                            "search_result_title": "",
                            "search_snippet": "",
                            "company_verification": "Not checked",
                            "linkedin_company_page": linkedin["url"],
                            "linkedin_verification": linkedin["verification"],
                            "search_error": search_error,
                        }
                    )
                    career_csv.flush()

                _safe_print(
                    f"{association}: {index}/{len(members)} "
                    f"({member})",
                    sys.stderr,
                )
                if args.delay:
                    time.sleep(args.delay)

    _safe_print(
        f"Wrote {searched} member searches to {args.output} and job details "
        f"to {args.jobs_output} ({errors} search or page errors).",
        sys.stdout,
    )
    return 1 if errors else 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return run(args)
    except (RuntimeError, ValueError) as exc:
        _safe_print(f"Error: {exc}", sys.stderr)
        return 1

def get_company_website(company_name):
    duckduckgo_url = f"https://duckduckgo.com/?q={company_name}+website"
    response = requests.get(duckduckgo_url)
    data = response.json()
    if "Link" in data["RelatedTopics"]:
        return data["RelatedTopics"]["Link"]
    else:
        return None

def member_career_search(member_id):
    # ... rest of the function remains the same ...

    company_website = get_company_website(member_name)
    if company_website:
        print(f"Company website: {company_website}")
    else:
        print(f"Company website not found for {member_name}")


if __name__ == "__main__":
    raise SystemExit(main())
