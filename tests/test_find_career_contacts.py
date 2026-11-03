import argparse
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import find_career_contacts as contacts


class CareerContactTests(unittest.TestCase):
    def test_extracts_public_email_phone_and_contact_page(self):
        parser = contacts.ContactPageParser()
        parser.feed(
            '<p>Recruiting: jobs@example.com; call (416) 555-0123.</p>'
            '<a href="mailto:talent@example.com">Email talent</a>'
            '<a href="tel:+1-416-555-0199">Call HR</a>'
            '<a href="/contact">Contact us</a>'
            '<script>hidden@example.com</script>'
        )

        emails, phones, contact_pages = contacts._extract_contacts(
            "https://example.com/careers", parser
        )

        self.assertEqual(
            emails, ["jobs@example.com", "talent@example.com"]
        )
        self.assertEqual(
            phones, ["+1-416-555-0199", "(416) 555-0123"]
        )
        self.assertEqual(contact_pages, ["https://example.com/contact"])

    def test_run_writes_one_row_for_every_input_company(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "companies.csv"
            output_path = Path(directory) / "contacts.csv"
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
                writer.writerows(
                    [
                        {
                            "association": "CADSI",
                            "member": "Example Inc.",
                            "likely_company_homepage": "https://example.com/",
                            "career_page_urls": "https://example.com/careers",
                        },
                        {
                            "association": "AIAC",
                            "member": "No Careers Ltd.",
                            "likely_company_homepage": "",
                            "career_page_urls": "",
                        },
                    ]
                )
            args = argparse.Namespace(
                input=input_path,
                output=output_path,
                limit=0,
                delay=0,
            )
            with patch.object(
                contacts,
                "_fetch_page",
                return_value=(
                    '<a href="mailto:jobs@example.com">Recruiting email</a>'
                ),
            ):
                result = contacts.run(args)

            with output_path.open(newline="", encoding="utf-8-sig") as output:
                rows = list(csv.DictReader(output))

        self.assertEqual(result, 0)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["career_contact_emails"], "jobs@example.com")
        self.assertEqual(rows[0]["career_contact_status"], "Public contact info found")
        self.assertEqual(rows[1]["career_contact_status"], "No careers page URL listed")


if __name__ == "__main__":
    unittest.main()
