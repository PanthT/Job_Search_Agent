"""Resume missing corporate website discovery without an API key."""
import argparse
import csv
import os
import re
from urllib.parse import urlparse
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import member_career_search as agent


class RetryingSearch:
    """Use independent engines so one unavailable backend cannot stop discovery."""
    def results(self, query, max_results=8):
        from ddgs import DDGS
        errors = []
        completed = False
        for backend in ("bing", "brave", "duckduckgo"):
            for attempt in range(2):
                try:
                    results = DDGS(timeout=8).text(query, max_results=max_results, backend=backend)
                    completed = True
                    if results:
                        return [{"link": r.get("href", ""), "title": r.get("title", ""),
                                 "snippet": r.get("body", "")} for r in results]
                    break
                except Exception as exc:
                    if str(exc).strip().lower().rstrip(".") == "no results found":
                        completed = True
                        break
                    errors.append(f"{backend}: {exc}")
                    if attempt == 0:
                        time.sleep(1)
        if completed:
            return []
        raise RuntimeError("; ".join(errors))


def save(path, fields, rows):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", newline="", encoding="utf-8-sig",
                                         dir=path.parent, delete=False) as target:
            temporary = target.name
            writer = csv.DictWriter(target, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def repair(row):
    try:
        result = agent.find_company_website(RetryingSearch(), row["member"])
        return result, ""
    except Exception as exc:
        return {"url": "", "verification": "Search failed"}, str(exc)


def run(args):
    if args.limit < 0 or args.workers < 1:
        raise ValueError("limit must be nonnegative and workers must be positive")
    with args.input.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        fields = list(reader.fieldnames or [])
        if not {"member", "likely_company_homepage", "verification"} <= set(fields):
            raise ValueError("Missing required company columns")
        rows = list(reader)
    for field in ("homepage_checked_at", "homepage_source", "homepage_search_error", "homepage_candidate", "homepage_review_status"):
        if field not in fields:
            fields.append(field)
    indexes = [i for i, row in enumerate(rows)
               if not row["likely_company_homepage"].strip()
               and (args.retry_checked or not row.get("homepage_checked_at"))]
    if args.limit:
        indexes = indexes[:args.limit]
    # A dated backup is created once per invocation, before any modification.
    backup = args.input.with_name(args.input.name + "." + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".bak")
    backup.write_bytes(args.input.read_bytes())
    found = 0
    agent.REQUEST_TIMEOUT = 8
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(repair, rows[i]): i for i in indexes}
        for count, future in enumerate(as_completed(futures), 1):
            index = futures[future]
            result, error = future.result()
            row = rows[index]
            row["homepage_checked_at"] = datetime.now(timezone.utc).isoformat()
            row["homepage_search_error"] = error
            row["homepage_candidate"] = ""
            suspicious = bool(re.search(r"(?:-|_)(?:inc|ltd|corp|llc|limited)(?:-|_|$)", (urlparse(result["url"]).hostname or "").split(".")[0]))
            row["homepage_review_status"] = ""
            if result["url"] and not suspicious and result["verification"] in {"Found in page content", "Found in structured organization data"}:
                row["likely_company_homepage"] = result["url"]
                row["verification"] = result["verification"]
                row["homepage_source"] = "Site self-identifies as company; ownership not independently verified"
                row["homepage_review_status"] = "Needs ownership review"
                found += 1
            elif result["url"]:
                row["homepage_candidate"] = result["url"]
                row["homepage_review_status"] = "Needs manual review"
            save(args.input, fields, rows)
            print(f"{count}/{len(indexes)}: {row['member']}: {row['likely_company_homepage'] or result['verification']} ({found} added)", flush=True)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("likely_company_homepages.csv"))
    parser.add_argument("--limit", type=int, default=0, help="0 means all missing companies")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--retry-checked", action="store_true")
    raise SystemExit(run(parser.parse_args()))
