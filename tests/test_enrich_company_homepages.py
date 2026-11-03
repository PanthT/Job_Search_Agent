import argparse
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import enrich_company_homepages as enricher


class EnrichCompanyHomepagesTests(unittest.TestCase):
    def _write_csv(
        self, path: Path, fields: list[str], rows: list[dict[str, str]]
    ) -> None:
        with path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def test_run_adds_career_pages_and_linkedin_without_dropping_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "companies.csv"
            career_results = Path(directory) / "careers.csv"
            source_fields = [
                "association",
                "member",
                "likely_company_homepage",
                "verification",
                "association_directory_url",
                "linkedin_company_page",
            ]
            self._write_csv(
                source,
                source_fields,
                [
                    {
                        "association": "CADSI",
                        "member": "Example Inc.",
                        "likely_company_homepage": "https://example.com/",
                        "verification": "Found in page content",
                        "association_directory_url": "https://association.test/",
                        "linkedin_company_page": "",
                    }
                ],
            )
            self._write_csv(
                career_results,
                [
                    "association",
                    "member",
                    "likely_company_homepage",
                    "careers_page_url",
                    "status",
                    "search_error",
                ],
                [
                    {
                        "association": "CADSI",
                        "member": "Example Inc.",
                        "likely_company_homepage": "https://example.com/",
                        "careers_page_url": "https://example.com/careers",
                        "status": "Found on company homepage",
                        "search_error": "",
                    }
                ],
            )
            args = argparse.Namespace(
                input=source,
                career_results=career_results,
                limit=0,
                delay=0,
            )
            with (
                patch.object(enricher, "_create_web_search", return_value=object()),
                patch.object(
                    enricher,
                    "find_company_linkedin",
                    return_value={
                        "url": "https://www.linkedin.com/company/example/",
                        "verification": "Name matched in LinkedIn search result",
                    },
                ),
            ):
                result = enricher.run(args)

            with source.open(newline="", encoding="utf-8-sig") as updated:
                rows = list(csv.DictReader(updated))
            self.assertEqual(result, 0)
            self.assertTrue(set(source_fields).issubset(rows[0]))
            self.assertEqual(
                rows[0]["career_page_urls"], "https://example.com/careers"
            )
            self.assertEqual(
                rows[0]["linkedin_company_page"],
                "https://www.linkedin.com/company/example/",
            )
            self.assertEqual(
                rows[0]["association_directory_url"],
                "https://association.test/",
            )

    def test_search_errors_are_recorded_and_script_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "companies.csv"
            career_results = Path(directory) / "careers.csv"
            self._write_csv(
                source,
                [
                    "association",
                    "member",
                    "likely_company_homepage",
                    "linkedin_company_page",
                ],
                [
                    {
                        "association": "CADSI",
                        "member": "Example Inc.",
                        "likely_company_homepage": "https://example.com/",
                        "linkedin_company_page": "",
                    }
                ],
            )
            self._write_csv(
                career_results,
                [
                    "association",
                    "member",
                    "likely_company_homepage",
                    "careers_page_url",
                    "status",
                    "search_error",
                ],
                [],
            )
            args = argparse.Namespace(
                input=source,
                career_results=career_results,
                limit=0,
                delay=0,
            )
            with (
                patch.object(enricher, "_create_web_search", return_value=object()),
                patch.object(
                    enricher,
                    "find_company_linkedin",
                    side_effect=RuntimeError("Search unavailable"),
                ),
            ):
                result = enricher.run(args)

            with source.open(newline="", encoding="utf-8-sig") as updated:
                row = next(csv.DictReader(updated))

        self.assertEqual(result, 1)
        self.assertEqual(row["linkedin_verification"], "Search failed")
        self.assertIn("Search unavailable", row["enrichment_error"])

    def test_existing_linkedin_and_completed_no_match_are_not_researched(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "companies.csv"
            career_results = Path(directory) / "careers.csv"
            fields = [
                "association",
                "member",
                "likely_company_homepage",
                "linkedin_company_page",
                "linkedin_verification",
            ]
            self._write_csv(
                source,
                fields,
                [
                    {
                        "association": "CADSI",
                        "member": "Linked Inc.",
                        "likely_company_homepage": "https://linked.test/",
                        "linkedin_company_page": "https://linkedin.com/company/linked",
                        "linkedin_verification": "Name matched in LinkedIn search result",
                    },
                    {
                        "association": "CADSI",
                        "member": "No Match Inc.",
                        "likely_company_homepage": "https://nomatch.test/",
                        "linkedin_company_page": "",
                        "linkedin_verification": "No matching page found",
                    },
                ],
            )
            self._write_csv(
                career_results,
                [
                    "association",
                    "member",
                    "likely_company_homepage",
                    "careers_page_url",
                    "status",
                    "search_error",
                ],
                [],
            )
            args = argparse.Namespace(
                input=source,
                career_results=career_results,
                limit=0,
                delay=0,
            )
            with patch.object(
                enricher, "_create_web_search", side_effect=AssertionError
            ):
                result = enricher.run(args)

            with source.open(newline="", encoding="utf-8-sig") as updated:
                rows = list(csv.DictReader(updated))

        self.assertEqual(result, 0)
        self.assertEqual(rows[1]["linkedin_verification"], "No matching page found")


if __name__ == "__main__":
    unittest.main()
