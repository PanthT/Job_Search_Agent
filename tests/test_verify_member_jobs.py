import csv
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import verify_member_jobs


class VerifyMemberJobsTests(unittest.TestCase):
    def test_legacy_directory_url_is_written_as_member_website(self):
        row = {
            "member": "Example Company",
            "directory_url": "https://example.com/",
        }
        parser = SimpleNamespace(links=[], text=[], headings=[])

        with (
            patch.object(verify_member_jobs, "_fetch_parser", return_value=parser),
            patch.object(
                verify_member_jobs.mcs,
                "verify_company_on_page",
                return_value="Found in page content",
            ),
        ):
            result = verify_member_jobs.verify_row(row, {})

        self.assertEqual(result["member_website"], "https://example.com/")

    def test_member_website_takes_precedence_over_legacy_directory_url(self):
        row = {
            "member": "Example Company",
            "member_website": "https://member.example/",
            "directory_url": "https://old.example/",
        }
        parser = SimpleNamespace(links=[], text=[], headings=[])

        with (
            patch.object(
                verify_member_jobs,
                "_fetch_parser",
                return_value=parser,
            ) as fetch_parser,
            patch.object(
                verify_member_jobs.mcs,
                "verify_company_on_page",
                return_value="Found in page content",
            ),
        ):
            result = verify_member_jobs.verify_row(row, {})

        self.assertEqual(result["member_website"], "https://member.example/")
        fetch_parser.assert_called_once_with("https://member.example/")

    def test_only_fetches_member_website_not_careers_or_job_urls(self):
        row = {
            "member": "Example Company",
            "member_website": "https://member.example/",
            "careers_page_url": "https://member.example/careers",
            "job_url": "https://another.example/job",
        }
        parser = SimpleNamespace(
            links=[{"href": "https://member.example/careers"}],
            text=[],
            headings=[],
        )

        with (
            patch.object(
                verify_member_jobs,
                "_fetch_parser",
                return_value=parser,
            ) as fetch_parser,
            patch.object(
                verify_member_jobs.mcs,
                "verify_company_on_page",
                return_value="Found in page content",
            ),
        ):
            result = verify_member_jobs.verify_row(row, {})

        fetch_parser.assert_called_once_with("https://member.example/")
        self.assertEqual(result["verified_on_company_website"],
                         "Verified - member website confirms")
        self.assertNotIn("careers_page_url", result)
        self.assertNotIn("job_url", result)
        self.assertEqual(
            result["website_links_to_careers"], "Yes - links to member.example"
        )

    def test_verified_csv_omits_careers_and_job_url_columns(self):
        parser = SimpleNamespace(links=[], text=[], headings=[])
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "jobs.csv"
            output_path = Path(directory) / "verified.csv"
            with input_path.open("w", newline="", encoding="utf-8") as csv_file:
                writer = csv.DictWriter(
                    csv_file,
                    fieldnames=[
                        "member",
                        "member_website",
                        "job_title",
                        "careers_page_url",
                        "job_url",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "member": "Example Company",
                        "member_website": "https://member.example/",
                        "job_title": "Engineer",
                        "careers_page_url": "https://member.example/careers",
                        "job_url": "https://jobs.example/engineer",
                    }
                )

            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "verify_member_jobs.py",
                        "--input",
                        str(input_path),
                        "--output",
                        str(output_path),
                    ],
                ),
                patch.object(
                    verify_member_jobs,
                    "_fetch_parser",
                    return_value=parser,
                ),
                patch.object(
                    verify_member_jobs.mcs,
                    "verify_company_on_page",
                    return_value="Found in page content",
                ),
            ):
                self.assertEqual(verify_member_jobs.main(), 0)

            with output_path.open(encoding="utf-8-sig", newline="") as csv_file:
                reader = csv.DictReader(csv_file)
                self.assertNotIn("careers_page_url", reader.fieldnames)
                self.assertNotIn("job_url", reader.fieldnames)
                row = next(reader)

        self.assertEqual(row["job_title"], "Engineer")
        self.assertEqual(row["member_website"], "https://member.example/")


if __name__ == "__main__":
    unittest.main()
