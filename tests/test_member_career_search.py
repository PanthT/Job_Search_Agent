import argparse
import csv
import io
import logging
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, call, patch

import member_career_search as career_search


class FakeSearch:
    def __init__(self, results):
        self.search_results = results
        self.query = None
        self.max_results = None

    def results(self, query, max_results):
        self.query = query
        self.max_results = max_results
        return self.search_results


class LoggingFakeSearch(FakeSearch):
    def results(self, query, max_results):
        print("Search result for Québec Énergie")
        return super().results(query, max_results)


class DirectoryParserTests(unittest.TestCase):
    def test_fetch_page_percent_encodes_unicode_url_components(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b"<html>ok</html>"
        with patch.object(
            career_search, "urlopen", return_value=response
        ) as open_url:
            page = career_search._fetch_page(
                "https://example.com/caf\u00e9?q=Montr\u00e9al"
            )

        self.assertEqual(page, "<html>ok</html>")
        request = open_url.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://example.com/caf%C3%A9?q=Montr%C3%A9al",
        )
        request.full_url.encode("ascii")

    def test_fetch_page_falls_back_to_curl_after_python_connection_error(self):
        response = Mock(
            returncode=0,
            stdout=b"<html>directory page</html>",
            stderr=b"",
        )
        with (
            patch.object(
                career_search,
                "urlopen",
                side_effect=career_search.URLError("connection reset"),
            ),
            patch.object(
                career_search.subprocess, "run", return_value=response
            ) as run_curl,
        ):
            page = career_search._fetch_page(
                "https://www.defenceandsecurity.ca/en/membership/members-list"
            )

        self.assertEqual(page, "<html>directory page</html>")
        self.assertEqual(run_curl.call_args.args[0][0], "curl")

    def test_aiac_parser_reads_members_and_next_page(self):
        parser = career_search.DirectoryParser("aiac")
        parser.feed(
            '<ul class="member-list"><li data-province="ON">'
            '<a href="/members/example/">Example &amp; Co.</a></li></ul>'
            '<a fs-list-element="page-next" href="?page=2">Next</a>'
        )

        self.assertEqual(parser.members, ["Example & Co."])
        self.assertEqual(parser.next_page, "?page=2")

    def test_cadsi_parser_reads_card_names_and_next_page(self):
        parser = career_search.DirectoryParser("cadsi")
        parser.feed(
            '<div role="listitem" class="w-dyn-item"><div><div '
            'class="member-logo-card"><div class="body-small text-center">'
            "Example Defence</div></div></div></div>"
            '<a fs-list-element="page-next" href="?page=2">Next</a>'
        )

        self.assertEqual(parser.members, ["Example Defence"])
        self.assertEqual(parser.next_page, "?page=2")

    def test_get_members_follows_pagination_and_deduplicates(self):
        first_page = (
            '<ul class="member-list"><li><a>Example One</a></li>'
            '<li><a>Example Two</a></li></ul>'
            '<a fs-list-element="page-next" href="?page=2">Next</a>'
        )
        second_page = (
            '<ul class="member-list"><li><a>Example Two</a></li>'
            '<li><a>Example Three</a></li></ul>'
        )
        with patch.object(
            career_search,
            "_fetch_page",
            side_effect=[first_page, second_page],
        ) as fetch_page:
            members = career_search.get_members(
                "aiac", "https://aiac.ca/membership/member-directory/"
            )

        self.assertEqual(members, ["Example One", "Example Two", "Example Three"])
        self.assertEqual(
            fetch_page.call_args_list,
            [
                call("https://aiac.ca/membership/member-directory/"),
                call("https://aiac.ca/membership/member-directory/?page=2"),
            ],
        )


class CareerSearchTests(unittest.TestCase):
    def test_search_keeps_careers_pages_and_filters_individual_postings(self):
        search = FakeSearch(
            [
                {
                    "title": "Example Careers",
                    "link": "https://example.com/careers",
                    "snippet": "Join our team",
                },
                {
                    "title": "Example Engineer Job",
                    "link": "https://example.com/job/123",
                    "snippet": "Career opportunity",
                },
                {
                    "title": "Example Jobs",
                    "link": "https://example.com/jobs",
                    "snippet": "View current openings",
                },
            ]
        )

        results = career_search.find_career_pages(search, "Example Company")

        self.assertEqual(
            [result["url"] for result in results],
            [
                "https://example.com/careers",
                "https://example.com/jobs",
            ],
        )
        self.assertIn('"Example Company" careers jobs official', search.query)
        self.assertEqual(search.max_results, 8)

    def test_search_no_results_returns_empty_list(self):
        search = FakeSearch([])

        self.assertEqual(career_search.find_career_pages(search, "Unknown"), [])

    def test_find_career_pages_drops_job_boards(self):
        search = FakeSearch(
            [
                {
                    "title": "Example Careers",
                    "link": "https://careers.example.com/",
                    "snippet": "Join our team",
                },
                {
                    "title": "Example jobs at LinkedIn",
                    "link": "https://www.linkedin.com/company/example/jobs/",
                    "snippet": "Join our team",
                },
                {
                    "title": "Example job at Indeed",
                    "link": "https://ca.indeed.com/viewjob?jk=abc",
                    "snippet": "Join our team",
                },
            ]
        )

        results = career_search.find_career_pages(search, "Example Company")

        self.assertEqual(
            [result["url"] for result in results],
            ["https://careers.example.com/"],
        )

    def test_find_career_pages_opt_out_keeps_job_boards(self):
        search = FakeSearch(
            [
                {
                    "title": "Example jobs",
                    "link": "https://www.linkedin.com/company/example/jobs/",
                    "snippet": "Join our team",
                },
            ]
        )

        results = career_search.find_career_pages(
            search, "Example Company", company_site_only=False
        )

        self.assertEqual(len(results), 1)

    def test_find_career_pages_requires_company_domain_when_known(self):
        search = FakeSearch(
            [
                {
                    "title": "Unrelated Careers",
                    "link": "https://careers.unrelated.com/",
                    "snippet": "Example Company careers",
                },
                {
                    "title": "Example Careers",
                    "link": "https://careers.example.com/",
                    "snippet": "Join our team",
                },
            ]
        )

        results = career_search.find_career_pages(
            search, "Example Company", company_website_url="https://example.com/"
        )

        self.assertEqual(
            [result["url"] for result in results],
            ["https://careers.example.com/"],
        )

    def test_is_company_job_url_allows_ats_and_rejects_boards(self):
        self.assertTrue(
            career_search.is_company_job_url(
                "https://jobs.example.com/openings/1", "https://example.com/", True
            )
        )
        self.assertTrue(
            career_search.is_company_job_url(
                "https://example.myworkdayjobs.com/en-US/job1",
                "https://example.com/",
                True,
            )
        )
        self.assertFalse(
            career_search.is_company_job_url(
                "https://ca.indeed.com/viewjob?jk=abc", "https://example.com/", True
            )
        )
        self.assertFalse(
            career_search.is_company_job_url(
                "https://other-company.com/jobs/1", "https://example.com/", True
            )
        )

    def test_search_transliterates_member_name_to_ascii(self):
        search = FakeSearch([])

        career_search.find_career_pages(search, "Québec Énergie Inc.")

        self.assertEqual(
            search.query,
            '"Quebec Energie Inc." careers jobs official',
        )

    def test_progress_output_escapes_names_for_ascii_terminal(self):
        output = io.BytesIO()
        stream = io.TextIOWrapper(output, encoding="ascii")
        career_search._safe_print("CADSI: 1/1 (Québec Énergie)", stream)
        stream.flush()

        self.assertEqual(
            output.getvalue().decode("ascii"),
            r"CADSI: 1/1 (Qu\xe9bec \xc9nergie)" + "\n",
        )

    def test_run_writes_career_results_for_both_directories(self):
        search = FakeSearch(
            [
                {
                    "title": "Example Careers",
                    "link": "https://example.com/careers",
                    "snippet": "Current career opportunities",
                }
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "members.csv"
            jobs_output = Path(directory) / "jobs.csv"
            args = argparse.Namespace(
                output=output,
                jobs_output=jobs_output,
                limit=1,
                delay=0,
                max_job_pages=career_search.MAX_JOB_PAGES_PER_SITE,
            )
            progress = io.StringIO()
            with redirect_stderr(progress):
                with (
                    patch.object(
                        career_search,
                        "get_members",
                        side_effect=[["CADSI Example"], ["AIAC Example"]],
                    ),
                    patch.object(career_search, "_create_web_search", return_value=search),
                    patch.object(
                        career_search,
                        "crawl_career_page",
                        return_value=[
                            {
                                "association": "Test",
                                "member": "Test",
                                "job_title": "",
                                "description": "",
                                "years_of_experience": "",
                                "salary": "",
                                "job_url": "",
                                "careers_page_url": "https://example.com/careers",
                                "status": "No job postings found on this page",
                                "company_verification": "Not found",
                                "career_page_company_verification": "Not found",
                            }
                        ],
                    ),
                    patch.object(
                        career_search,
                        "find_company_linkedin",
                        return_value={
                            "url": "https://www.linkedin.com/company/example/",
                            "verification": "Name matched in LinkedIn search result",
                        },
                    ),
                    patch.object(
                        career_search,
                        "find_company_website",
                        return_value={
                            "url": "https://example.com/",
                            "verification": "Found in page content",
                        },
                    ),
                ):
                    result = career_search.run(args)

            with output.open(encoding="utf-8-sig", newline="") as csv_file:
                rows = list(csv.DictReader(csv_file))
            with jobs_output.open(encoding="utf-8-sig", newline="") as csv_file:
                job_rows = list(csv.DictReader(csv_file))

        self.assertEqual(result, 0)
        self.assertEqual([row["association"] for row in rows], ["CADSI", "AIAC"])
        self.assertEqual(
            [row["association_directory_url"] for row in rows],
            [
                career_search.CADSI_DIRECTORY_URL,
                career_search.AIAC_DIRECTORY_URL,
            ],
        )
        self.assertEqual(
            [row["directory_url"] for row in rows],
            ["https://example.com/"] * 2,
        )
        self.assertEqual(
            [row["member_website"] for row in rows],
            ["https://example.com/"] * 2,
        )
        self.assertEqual(
            [row["company_website_verification"] for row in rows],
            ["Found in page content"] * 2,
        )
        self.assertEqual(
            [row["potential_careers_page"] for row in rows],
            ["https://example.com/careers", "https://example.com/careers"],
        )
        self.assertEqual(
            [row["company_verification"] for row in rows],
            ["Not found", "Not found"],
        )
        self.assertEqual(
            [row["linkedin_company_page"] for row in rows],
            ["https://www.linkedin.com/company/example/"] * 2,
        )
        self.assertEqual(
            [row["linkedin_verification"] for row in rows],
            ["Name matched in LinkedIn search result"] * 2,
        )
        self.assertEqual(
            [row["status"] for row in job_rows],
            ["No job postings found on this page"] * 2,
        )
        self.assertEqual(
            [row["linkedin_company_page"] for row in job_rows],
            ["https://www.linkedin.com/company/example/"] * 2,
        )
        self.assertEqual(
            [row["linkedin_verification"] for row in job_rows],
            ["Name matched in LinkedIn search result"] * 2,
        )
        self.assertEqual(
            [row["member_website"] for row in job_rows],
            ["https://example.com/"] * 2,
        )
        self.assertEqual(
            progress.getvalue(),
            "CADSI: 1/1 (CADSI Example)\nAIAC: 1/1 (AIAC Example)\n",
        )

    def test_run_handles_search_output_with_ascii_console_and_accented_member(self):
        search = LoggingFakeSearch([])
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(
                output=Path(directory) / "members.csv",
                jobs_output=Path(directory) / "jobs.csv",
                limit=1,
                delay=0,
                max_job_pages=career_search.MAX_JOB_PAGES_PER_SITE,
            )
            output_bytes = io.BytesIO()
            ascii_stdout = io.TextIOWrapper(output_bytes, encoding="ascii")
            progress = io.StringIO()
            with redirect_stdout(ascii_stdout), redirect_stderr(progress):
                with (
                    patch.object(
                        career_search,
                        "get_members",
                        side_effect=[["Québec Énergie"], []],
                    ),
                    patch.object(
                        career_search, "_create_web_search", return_value=search
                    ),
                ):
                    result = career_search.run(args)
                ascii_stdout.flush()

            self.assertEqual(result, 0)
            self.assertIn(b"Qu\\xe9bec", output_bytes.getvalue())
            self.assertIn("Québec Énergie", progress.getvalue())

    def test_console_configuration_updates_existing_ascii_logging_handlers(self):
        output_bytes = io.BytesIO()
        ascii_stderr = io.TextIOWrapper(output_bytes, encoding="ascii")
        handler = logging.StreamHandler(ascii_stderr)
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        try:
            with redirect_stderr(ascii_stderr):
                career_search._configure_console_encoding()
                handler.emit(
                    logging.LogRecord(
                        "test",
                        logging.ERROR,
                        __file__,
                        1,
                        "Search failed for Québec Énergie",
                        (),
                        None,
                    )
                )
                handler.flush()

            self.assertIn(
                b"Qu\\xe9bec",
                output_bytes.getvalue(),
            )
        finally:
            root_logger.removeHandler(handler)
            handler.close()

    def test_console_configuration_wraps_dunder_stderr_logging_handlers(self):
        output_bytes = io.BytesIO()
        ascii_stderr = io.TextIOWrapper(output_bytes, encoding="ascii")
        handler = logging.StreamHandler(ascii_stderr)
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        try:
            with patch.object(career_search.sys, "__stderr__", ascii_stderr):
                career_search._configure_console_encoding()
                handler.emit(
                    logging.LogRecord(
                        "test",
                        logging.ERROR,
                        __file__,
                        1,
                        "Search failed for Québec Énergie",
                        (),
                        None,
                    )
                )
                handler.flush()

            self.assertIn(b"Qu\\xe9bec", output_bytes.getvalue())
        finally:
            root_logger.removeHandler(handler)
            handler.close()

    def test_crawl_career_page_extracts_job_details(self):
        careers_page = (
            '<h1>Example Aerospace Careers</h1>'
            '<a href="/jobs/senior-engineer">Senior Engineer</a>'
        )
        job_page = """
            <html><h1>Senior Engineer</h1>
            <script type="application/ld+json">
            {
              "@context": "https://schema.org",
              "@type": "JobPosting",
              "title": "Senior Engineer",
              "description": "<p>Example Aerospace builds aerospace systems. 5+ years of experience.</p>",
              "hiringOrganization": {
                "@type": "Organization",
                "name": "Example Aerospace"
              },
              "experienceRequirements": {
                "@type": "OccupationalExperienceRequirements",
                "monthsOfExperience": 60
              },
              "baseSalary": {
                "@type": "MonetaryAmount",
                "currency": "CAD",
                "value": {
                  "@type": "QuantitativeValue",
                  "minValue": 100000,
                  "maxValue": 120000,
                  "unitText": "YEAR"
                }
              }
            }
            </script></html>
        """
        with patch.object(
            career_search,
            "_fetch_page",
            side_effect=[careers_page, job_page],
        ) as fetch_page:
            rows = career_search.crawl_career_page(
                "AIAC",
                "Example Aerospace",
                "https://example.com/careers",
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["job_title"], "Senior Engineer")
        self.assertEqual(
            rows[0]["description"],
            "Example Aerospace builds aerospace systems. "
            "5+ years of experience.",
        )
        self.assertEqual(rows[0]["years_of_experience"], "5 years")
        self.assertEqual(rows[0]["salary"], "CAD 100,000-120,000 per year")
        self.assertEqual(
            rows[0]["job_url"],
            "https://example.com/jobs/senior-engineer",
        )
        self.assertEqual(rows[0]["company_verification"], "Found in structured organization data")
        self.assertEqual(
            rows[0]["career_page_company_verification"],
            "Found in page content",
        )
        self.assertEqual(fetch_page.call_count, 2)

    def test_crawl_falls_back_to_visible_text_for_experience_and_salary(self):
        careers_page = (
            '<a href="/jobs/analyst">Business Analyst</a>'
        )
        job_page = """
            <html><h1>Business Analyst</h1>
            <main><p>Requires at least 4 years of experience.</p>
            <p>Salary: CAD $85,000 to $105,000 per year.</p></main></html>
        """
        with patch.object(
            career_search,
            "_fetch_page",
            side_effect=[careers_page, job_page],
        ):
            rows = career_search.crawl_career_page(
                "CADSI",
                "Example Systems",
                "https://example.com/careers",
                delay=0,
            )

        self.assertEqual(rows[0]["job_title"], "Business Analyst")
        self.assertIn("4 years of experience", rows[0]["description"])
        self.assertEqual(rows[0]["years_of_experience"], "4 years")
        self.assertEqual(rows[0]["salary"], "CAD 85,000-105,000 per year")

    def test_experience_and_salary_text_fallbacks(self):
        self.assertEqual(
            career_search._experience_from_posting(
                {}, "Requires 3-5 years of relevant experience."
            ),
            "3-5 years",
        )
        self.assertEqual(
            career_search._salary_from_text(
                "Compensation ranges from CAD $80,000 to $100,000 per year."
            ),
            "CAD 80,000-100,000 per year",
        )

    def test_company_verification_reports_page_content_domain_and_missing(self):
        page = career_search.JobPageParser()
        page.feed("<html><title>Acme Aerospace Careers</title></html>")
        self.assertEqual(
            career_search.verify_company_on_page(
                "Acme Aerospace Inc.", page, "https://jobs.example.com/careers"
            ),
            "Found in page content",
        )

        empty_page = career_search.JobPageParser()
        self.assertEqual(
            career_search.verify_company_on_page(
                "Acme Aerospace", empty_page, "https://careers.acme-aerospace.com/jobs"
            ),
            "Found in domain",
        )
        self.assertEqual(
            career_search.verify_company_on_page(
                "Acme Aerospace", empty_page, "https://careers.other-company.com/jobs"
            ),
            "Not found",
        )

    def test_linkedin_search_only_returns_matching_company_pages(self):
        search = FakeSearch(
            [
                {
                    "title": "Example Aerospace | LinkedIn",
                    "link": "https://www.linkedin.com/company/example-aerospace/",
                    "snippet": "Learn about Example Aerospace.",
                },
                {
                    "title": "Example Aerospace Careers | LinkedIn",
                    "link": "https://www.linkedin.com/jobs/example-aerospace/",
                    "snippet": "Jobs at Example Aerospace.",
                },
            ]
        )

        result = career_search.find_company_linkedin(search, "Example Aerospace Inc.")

        self.assertEqual(
            result,
            {
                "url": "https://www.linkedin.com/company/example-aerospace/",
                "verification": "Name matched in LinkedIn search result",
            },
        )
        self.assertEqual(
            search.query,
            'site:linkedin.com/company "Example Aerospace Inc."',
        )

    def test_linkedin_query_transliterates_member_name_to_ascii(self):
        search = FakeSearch([])

        career_search.find_company_linkedin(search, "Québec Énergie Inc.")

        self.assertEqual(
            search.query,
            'site:linkedin.com/company "Quebec Energie Inc."',
        )

    def test_linkedin_search_rejects_unmatched_company_page(self):
        search = FakeSearch(
            [
                {
                    "title": "Unrelated Company | LinkedIn",
                    "link": "https://www.linkedin.com/company/unrelated/",
                    "snippet": "A different organization.",
                }
            ]
        )

        self.assertEqual(
            career_search.find_company_linkedin(search, "Example Aerospace"),
            {"url": "", "verification": "No matching page found"},
        )

    def test_company_website_search_excludes_associations_and_verifies_member(self):
        search = FakeSearch(
            [
                {
                    "title": "Acme Robotics - CADSI Members",
                    "link": "https://defenceandsecurity.ca/members/acme",
                    "snippet": "Acme Robotics member listing",
                },
                {
                    "title": "Acme Robotics | Official site",
                    "link": "https://www.acmerobotics.example/",
                    "snippet": "Autonomous systems and robotics",
                },
            ]
        )
        with patch.object(
            career_search,
            "_fetch_page",
            return_value="<html><title>Acme Robotics</title></html>",
        ) as fetch_page:
            result = career_search.find_company_website(
                search, "Acme Robotics Inc."
            )

        self.assertEqual(
            result,
            {
                "url": "https://www.acmerobotics.example/",
                "verification": "Found in page content",
            },
        )
        fetch_page.assert_called_once_with("https://www.acmerobotics.example/")
        self.assertEqual(
            search.query,
            '"Acme Robotics Inc." official company website',
        )

    def test_company_website_search_does_not_return_unmatched_result(self):
        search = FakeSearch(
            [
                {
                    "title": "Unrelated Robotics | Official site",
                    "link": "https://unrelated.example/",
                    "snippet": "Independent robotics company",
                }
            ]
        )

        self.assertEqual(
            career_search.find_company_website(search, "Acme Robotics"),
            {"url": "", "verification": "No matching website found"},
        )


if __name__ == "__main__":
    unittest.main()
