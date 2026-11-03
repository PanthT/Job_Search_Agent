import argparse
import csv
import tempfile
import unittest
from pathlib import Path

import build_likely_company_homepages as builder


class BuildLikelyCompanyHomepagesTests(unittest.TestCase):
    def test_builds_records_including_rows_without_homepage_and_preserves_links(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "members.csv"
            output_path = Path(directory) / "homepages.csv"
            with input_path.open("w", newline="", encoding="utf-8") as source:
                writer = csv.DictWriter(
                    source,
                    fieldnames=[
                        "association",
                        "member",
                        "association_directory_url",
                        "member_website",
                        "company_website_verification",
                        "search_error",
                    ],
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {
                            "association": "CADSI",
                            "member": "Found Inc.",
                            "association_directory_url": "https://cadsi.test/",
                            "member_website": "https://found.test/",
                            "company_website_verification": "Found in page content",
                            "search_error": "",
                        },
                        {
                            "association": "AIAC",
                            "member": "Missing Inc.",
                            "association_directory_url": "https://aiac.test/",
                            "member_website": "",
                            "company_website_verification": "No matching website found",
                            "search_error": "Web lookup unavailable",
                        },
                    ]
                )
            with output_path.open("w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(
                    output,
                    fieldnames=[
                        "association",
                        "member",
                        "likely_company_homepage",
                        "linkedin_company_page",
                        "career_page_urls",
                        "custom_enrichment",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "association": "CADSI",
                        "member": "Found Inc.",
                        "likely_company_homepage": "https://old.test/",
                        "linkedin_company_page": "https://linkedin.test/found",
                        "career_page_urls": "https://found.test/careers",
                        "custom_enrichment": "keep me",
                    }
                )
            result = builder.run(
                argparse.Namespace(input=input_path, output=output_path)
            )

            with output_path.open(newline="", encoding="utf-8-sig") as output:
                rows = list(csv.DictReader(output))

        self.assertEqual(result, 0)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["likely_company_homepage"], "https://found.test/")
        self.assertEqual(
            rows[0]["career_page_urls"], "https://found.test/careers"
        )
        self.assertEqual(
            rows[0]["linkedin_company_page"], "https://linkedin.test/found"
        )
        self.assertEqual(rows[0]["custom_enrichment"], "keep me")
        self.assertEqual(rows[1]["association"], "AIAC")
        self.assertEqual(rows[1]["likely_company_homepage"], "")
        self.assertEqual(
            rows[1]["verification"], "No matching website found"
        )
        self.assertEqual(rows[1]["search_error"], "Web lookup unavailable")


if __name__ == "__main__":
    unittest.main()
