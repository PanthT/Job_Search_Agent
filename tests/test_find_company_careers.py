import argparse
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import find_company_careers as finder


class FindCompanyCareersTests(unittest.TestCase):
    def _write_input(self, path: Path, rows: list[dict[str, str]]) -> None:
        fields = ["association", "member", "likely_company_homepage"]
        with path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def test_run_writes_candidate_results_and_not_found_status(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "companies.csv"
            output_path = Path(directory) / "careers.csv"
            self._write_input(
                input_path,
                [
                    {
                        "association": "CADSI",
                        "member": "Example Inc.",
                        "likely_company_homepage": "https://example.com/",
                    },
                    {
                        "association": "AIAC",
                        "member": "No Jobs Ltd.",
                        "likely_company_homepage": "https://nojobs.example/",
                    },
                ],
            )
            args = argparse.Namespace(
                input=input_path,
                output=output_path,
                limit=0,
                delay=0,
            )
            with (
                patch.object(finder, "_create_web_search", return_value=object()),
                patch.object(
                    finder,
                    "_find_homepage_career_links",
                    side_effect=[
                        [
                            {
                                "url": "https://example.com/careers",
                                "title": "Careers",
                                "snippet": "Link discovered on the company homepage.",
                            }
                        ],
                        [],
                    ],
                ),
                patch.object(
                    finder,
                    "find_career_pages",
                    return_value=[],
                ) as find_pages,
            ):
                result = finder.run(args)

            with output_path.open(newline="", encoding="utf-8-sig") as output:
                rows = list(csv.DictReader(output))

        self.assertEqual(result, 0)
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            rows[0]["careers_page_url"], "https://example.com/careers"
        )
        self.assertEqual(
            rows[0]["status"],
            "Found on company homepage; destination not independently verified",
        )
        self.assertEqual(rows[1]["careers_page_url"], "")
        self.assertEqual(rows[1]["status"], "No likely careers page found")
        find_pages.assert_called_once()
        self.assertEqual(
            find_pages.call_args.kwargs,
            {
                "company_website_url": "https://nojobs.example/",
                "company_site_only": True,
            },
        )

    def test_run_records_search_errors_and_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "companies.csv"
            output_path = Path(directory) / "careers.csv"
            self._write_input(
                input_path,
                [
                    {
                        "association": "CADSI",
                        "member": "Example Inc.",
                        "likely_company_homepage": "https://example.com/",
                    }
                ],
            )
            args = argparse.Namespace(
                input=input_path,
                output=output_path,
                limit=0,
                delay=0,
            )
            with (
                patch.object(finder, "_create_web_search", return_value=object()),
                patch.object(finder, "_find_homepage_career_links", return_value=[]),
                patch.object(
                    finder,
                    "find_career_pages",
                    side_effect=RuntimeError("Search unavailable"),
                ),
            ):
                result = finder.run(args)

            with output_path.open(newline="", encoding="utf-8-sig") as output:
                rows = list(csv.DictReader(output))

        self.assertEqual(result, 1)
        self.assertEqual(rows[0]["status"], "Search failed")
        self.assertEqual(rows[0]["search_error"], "Search unavailable")

    def test_homepage_links_keep_career_pages_and_skip_job_boards(self):
        page = (
            '<a href="/careers">Careers</a>'
            '<a href="https://jobs.example.com/openings">Open positions</a>'
            '<a href="https://www.linkedin.com/company/example/jobs/">Jobs</a>'
            '<a href="/job/123">Senior engineer job</a>'
        )
        with patch.object(finder, "_fetch_page", return_value=page):
            results = finder._find_homepage_career_links(
                "https://example.com/"
            )

        self.assertEqual(
            [result["url"] for result in results],
            [
                "https://example.com/careers",
                "https://jobs.example.com/openings",
            ],
        )


if __name__ == "__main__":
    unittest.main()
