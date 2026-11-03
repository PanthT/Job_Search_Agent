import argparse
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import find_company_contact_emails as finder


class CompanyContactEmailTests(unittest.TestCase):
    def test_extracts_general_and_hr_emails_and_same_site_contact_links(self):
        parser = finder.ContactPageParser()
        parser.feed(
            '<p>General inquiries: info@example.com</p>'
            '<a href="mailto:talent@example.com">HR recruiting</a>'
            '<a href="/contact-us">Contact Us</a>'
            '<a href="https://other.example.net/contact">External contact</a>'
        )

        emails, contact_links = finder._emails_from_page(
            "https://www.example.com/", parser
        )

        self.assertEqual(
            emails,
            {
                "info@example.com": False,
                "talent@example.com": True,
            },
        )
        self.assertEqual(
            contact_links, ["https://www.example.com/contact-us"]
        )

    def test_run_scans_homepage_and_contact_page_and_writes_one_row(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "companies.csv"
            output_path = Path(directory) / "emails.csv"
            with input_path.open("w", newline="", encoding="utf-8") as source:
                writer = csv.DictWriter(
                    source,
                    fieldnames=[
                        "association",
                        "member",
                        "likely_company_homepage",
                        "career_page_urls",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "association": "CADSI",
                        "member": "Example Inc.",
                        "likely_company_homepage": "https://example.com/",
                        "career_page_urls": "",
                    }
                )

            def fake_fetch(url: str) -> str:
                if url == "https://example.com/":
                    return (
                        '<a href="/contact">Contact</a>'
                        '<a href="mailto:info@example.com">Info</a>'
                    )
                if url == "https://example.com/contact":
                    return (
                        '<a href="mailto:hr@example.com">HR</a>'
                        '<a href="mailto:info@example.com">General inquiries</a>'
                    )
                raise AssertionError(f"Unexpected URL: {url}")

            with patch.object(finder, "_fetch_page", side_effect=fake_fetch):
                result = finder.run(
                    argparse.Namespace(
                        input=input_path,
                        output=output_path,
                        limit=0,
                        delay=0,
                        max_pages_per_company=4,
                    )
                )

            with output_path.open(newline="", encoding="utf-8-sig") as output:
                rows = list(csv.DictReader(output))

        self.assertEqual(result, 0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["public_contact_emails"], "info@example.com | hr@example.com")
        self.assertEqual(rows[0]["hr_recruiting_emails"], "hr@example.com")
        self.assertEqual(
            rows[0]["contact_page_urls"], "https://example.com/contact"
        )
        self.assertEqual(rows[0]["contact_email_status"], "Public email found")


if __name__ == "__main__":
    unittest.main()
