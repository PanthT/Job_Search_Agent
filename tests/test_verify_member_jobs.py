import unittest
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


if __name__ == "__main__":
    unittest.main()
