import csv
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock, patch

from docx import Document

import job_search_agent as agent


class JobSearchAgentTests(unittest.TestCase):
    def test_extract_search_queries_validates_and_deduplicates(self):
        with patch.object(
            agent,
            "_openrouter_generate",
            return_value=json.dumps({"search_queries": ["Data analyst", "Data analyst", "SQL"]}),
        ):
            queries = agent.extract_search_queries("resume text", "openrouter/free", "test-key")
        self.assertEqual(queries, ["Data analyst", "SQL"])

    def test_extract_search_queries_includes_target_industry(self):
        with patch.object(
            agent,
            "_openrouter_generate",
            return_value=json.dumps({"search_queries": ["data analyst"]}),
        ) as generate:
            agent.extract_search_queries(
                "resume text", "openrouter/free", "test-key", "healthcare"
            )
        self.assertIn("healthcare industry", generate.call_args.args[0])

    def test_assess_posting_rejects_invalid_score(self):
        posting = agent.Posting("1", "Analyst", "Acme", "Remote", "SQL", "https://example.com")
        with patch.object(
            agent,
            "_openrouter_generate",
            return_value=json.dumps(
                {
                    "score": 101,
                    "qualified": True,
                    "reason": "Strong match",
                    "matching_skills": ["SQL"],
                    "gaps": [],
                }
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "invalid assessment"):
                agent.assess_posting("resume", posting, "openrouter/free", "test-key")

    def test_create_report_includes_only_qualified_postings_above_threshold(self):
        qualified = agent.Posting(
            "1", "Analyst", "Acme", "Remote", "Build useful reports.", "https://example.com"
        )
        rejected = agent.Posting("2", "Engineer", "Other", "Remote", "", "https://example.org")
        assessments = [
            agent.Assessment(qualified, 80, True, "Good evidence", ["SQL"], []),
            agent.Assessment(rejected, 95, False, "Missing requirements", [], ["Python"]),
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "jobs.docx"
            agent.create_report(assessments, output, "Remote", ["analyst"], 60, "healthcare")
            document = Document(output)
        content = "\n".join(paragraph.text for paragraph in document.paragraphs)
        self.assertIn("Analyst", content)
        self.assertIn("Build useful reports.", content)
        self.assertIn("Industry focus: healthcare", content)
        self.assertIn("Link check: Not checked", content)
        self.assertNotIn("Engineer", content)

    def test_create_csv_report_exports_qualifying_jobs_with_details(self):
        passing = agent.Posting(
            "1",
            "Analyst",
            "Acme",
            "Remote",
            "Build reports",
            "https://example.com/job",
            80000,
            95000,
            True,
            "HTTP 200 - reachable",
            "https://careers.example.com/jobs/123",
        )
        failing = agent.Posting(
            "2", "Engineer", "Other", "Remote", "", "https://example.org/job"
        )
        assessments = [
            agent.Assessment(passing, 80, True, "Good fit", ["SQL", "Excel"], ["Cloud"]),
            agent.Assessment(failing, 99, False, "Not qualified", [], []),
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "jobs.csv"
            agent.create_csv_report(assessments, output, min_score=60)
            with output.open(encoding="utf-8-sig", newline="") as csv_file:
                rows = list(csv.DictReader(csv_file))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Analyst")
        self.assertEqual(rows[0]["salary_max"], "95000")
        self.assertEqual(rows[0]["matching_skills"], "SQL; Excel")
        self.assertEqual(rows[0]["url"], "https://example.com/job")
        self.assertEqual(rows[0]["link_verified"], "True")
        self.assertEqual(rows[0]["link_status"], "HTTP 200 - reachable")
        self.assertEqual(rows[0]["final_url"], "https://careers.example.com/jobs/123")

    def test_link_verification_visits_url_and_records_redirect(self):
        posting = agent.Posting(
            "1", "Analyst", "Acme", "Remote", "Analyze data", "https://example.com/job"
        )

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def getcode(self):
                return 200

            def geturl(self):
                return "https://careers.example.com/jobs/123"

            def read(self, _size):
                return b"job posting"

        class FakeOpener:
            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return FakeResponse()

        fake_opener = FakeOpener()
        with (
            patch.object(agent, "_validate_public_url"),
            patch.object(agent, "build_opener", return_value=fake_opener),
        ):
            verified = agent.verify_posting_link(posting)
        self.assertTrue(verified.link_verified)
        self.assertEqual(verified.link_status, "HTTP 200 - reachable")
        self.assertEqual(verified.final_url, "https://careers.example.com/jobs/123")
        self.assertEqual(fake_opener.request.full_url, posting.url)
        self.assertEqual(fake_opener.timeout, 12)

    def test_link_verification_reports_http_error(self):
        posting = agent.Posting(
            "1", "Analyst", "Acme", "Remote", "Analyze data", "https://example.com/job"
        )
        error = agent.HTTPError(
            posting.url, 404, "Not Found", {}, None
        )
        fake_opener = Mock()
        fake_opener.open.side_effect = error
        with (
            patch.object(agent, "_validate_public_url"),
            patch.object(agent, "build_opener", return_value=fake_opener),
        ):
            verified = agent.verify_posting_link(posting)
        self.assertFalse(verified.link_verified)
        self.assertIn("HTTP 404", verified.link_status)

    def test_link_verification_blocks_local_hosts_before_request(self):
        posting = agent.Posting(
            "1", "Analyst", "Acme", "Remote", "Analyze data", "http://127.0.0.1/admin"
        )
        with patch.object(agent, "build_opener") as build_opener:
            verified = agent.verify_posting_link(posting)
        self.assertFalse(verified.link_verified)
        self.assertIn("public IP", verified.link_status)
        build_opener.assert_not_called()

    def test_fetch_jobs_uses_web_search_and_maps_results(self):
        payload = {
            "postings": [
                {
                    "title": "Analyst",
                    "company": "Acme",
                    "location": "Remote",
                    "description": "Analyze data",
                    "url": "https://example.com/job",
                    "salary_min": "80000",
                },
                {
                    "title": "Analyst",
                    "company": "Acme",
                    "location": "Remote",
                    "description": "Duplicate result",
                    "url": "https://example.com/job#apply",
                },
            ]
        }
        with patch.object(
            agent, "_openrouter_generate", return_value=json.dumps(payload)
        ) as generate:
            jobs = agent.fetch_jobs(
                ["analyst"], "Remote", "openrouter/free", "test-key", 5, "healthcare"
            )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].company, "Acme")
        self.assertEqual(jobs[0].salary_min, 80000)
        self.assertIn("healthcare", generate.call_args.args[0])
        tools = generate.call_args.kwargs["tools"]
        self.assertEqual(tools[0]["type"], "openrouter:web_search")
        self.assertEqual(tools[0]["parameters"]["engine"], "exa")

    def test_assess_posting_includes_target_industry(self):
        posting = agent.Posting("1", "Analyst", "Acme", "Remote", "Analyze data", "https://example.com")
        response = {
            "score": 80,
            "qualified": True,
            "reason": "Role aligns with experience",
            "matching_skills": ["analysis"],
            "gaps": [],
        }
        with patch.object(
            agent, "_openrouter_generate", return_value=json.dumps(response)
        ) as generate:
            agent.assess_posting("resume", posting, "openrouter/free", "test-key", "healthcare")
        self.assertIn("The target industry is healthcare", generate.call_args.args[0])

    def test_run_requires_only_openrouter_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            resume = Path(directory) / "resume.txt"
            resume.write_text("Resume", encoding="utf-8")
            args = Namespace(
                resume=str(resume),
                max_results=15,
                min_score=60,
                model="openrouter/free",
                location="Remote",
                output="jobs.docx",
                csv_output="jobs.csv",
            )
            with patch.dict("os.environ", {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "OPENROUTER_API_KEY"):
                    agent.run(args)

    def test_openrouter_request_uses_bearer_key_and_chat_completions(self):
        payload = {"choices": [{"message": {"content": '{"ok": true}'}}]}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                return json.dumps(payload).encode()

        with patch.object(agent, "urlopen", return_value=FakeResponse()) as urlopen:
            result = agent._openrouter_generate("return JSON", "openrouter/free", "test-secret")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, agent.OPENROUTER_URL)
        self.assertEqual(request.get_header("Authorization"), "Bearer test-secret")
        self.assertEqual(json.loads(request.data)["messages"][0]["content"], "return JSON")
        self.assertEqual(result, '{"ok": true}')


if __name__ == "__main__":
    unittest.main()
