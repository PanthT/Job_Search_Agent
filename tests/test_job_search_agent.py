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
            "_ollama_generate",
            return_value=json.dumps({"search_queries": ["Data analyst", "Data analyst", "SQL"]}),
        ):
            queries = agent.extract_search_queries("resume text", "llama3.2:3b", "http://localhost:11434")
        self.assertEqual(queries, ["Data analyst", "SQL"])

    def test_extract_search_queries_includes_target_industry(self):
        with patch.object(
            agent,
            "_ollama_generate",
            return_value=json.dumps({"search_queries": ["data analyst"]}),
        ) as generate:
            agent.extract_search_queries(
                "resume text", "llama3.2:3b", "http://localhost:11434", "healthcare"
            )
        self.assertIn("healthcare industry", generate.call_args.args[0])

    def test_extract_search_queries_any_industry_does_not_restrict_sector(self):
        with patch.object(
            agent,
            "_ollama_generate",
            return_value=json.dumps({"search_queries": ["data analyst"]}),
        ) as generate:
            agent.extract_search_queries(
                "resume text", "llama3.2:3b", "http://localhost:11434", "any"
            )
        prompt = generate.call_args.args[0]
        self.assertIn("Search across any industry", prompt)
        self.assertNotIn("Focus the search terms on the any industry", prompt)

    def test_assess_posting_rejects_invalid_requirement_assessment(self):
        posting = agent.Posting("1", "Analyst", "Acme", "Remote", "SQL", "https://example.com")
        with patch.object(
            agent,
            "_ollama_generate",
            return_value=json.dumps(
                {
                    "is_individual_posting": True,
                    "industry_match": True,
                    "requirements": [
                        {
                            "criterion": "SQL",
                            "priority": "essential",
                            "status": "met",
                            "evidence": "SQL",
                        }
                    ],
                    "summary": "Strong match",
                }
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "invalid requirement"):
                agent.assess_posting("resume", posting, "llama3.2:3b", "http://localhost:11434")

    def test_create_report_includes_all_postings_and_marks_qualification(self):
        qualified = agent.Posting(
            "1", "Analyst", "Acme", "Remote", "Build useful reports.", "https://example.com"
        )
        rejected = agent.Posting("2", "Engineer", "Other", "Remote", "", "https://example.org")
        assessments = [
            agent.Assessment(
                qualified, 80, True, "Good evidence", ["SQL"], [], 100, 33
            ),
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
        self.assertIn("ATS-style match score: 80/100", content)
        self.assertIn("Required criteria coverage: 100%", content)
        self.assertIn("Preferred criteria coverage: 33%", content)
        self.assertIn("Link check: Not checked", content)
        self.assertIn("Engineer", content)
        self.assertIn("Assessment: Not qualified / needs review", content)
        self.assertTrue(
            any(
                rel.is_external and rel.target_ref == "https://example.com"
                for rel in document.part.rels.values()
            )
        )

    def test_create_csv_report_exports_all_jobs_with_details(self):
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
            agent.Assessment(
                passing, 80, True, "Good fit", ["SQL", "Excel"], ["Cloud"], 100, 33
            ),
            agent.Assessment(failing, 99, False, "Not qualified", [], []),
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "jobs.csv"
            agent.create_csv_report(assessments, output, min_score=60)
            with output.open(encoding="utf-8-sig", newline="") as csv_file:
                rows = list(csv.DictReader(csv_file))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["title"], "Analyst")
        self.assertEqual(rows[0]["salary_max"], "95000")
        self.assertEqual(rows[0]["required_coverage"], "100")
        self.assertEqual(rows[0]["preferred_coverage"], "33")
        self.assertEqual(rows[0]["matching_skills"], "SQL; Excel")
        self.assertEqual(rows[0]["url"], "https://example.com/job")
        self.assertEqual(
            rows[0]["apply_url"], "https://careers.example.com/jobs/123"
        )
        self.assertEqual(rows[0]["link_verified"], "True")
        self.assertEqual(rows[0]["link_status"], "HTTP 200 - reachable")
        self.assertEqual(rows[0]["final_url"], "https://careers.example.com/jobs/123")
        rejected_row = next(row for row in rows if row["title"] == "Engineer")
        self.assertEqual(rejected_row["qualified"], "False")
        self.assertEqual(rejected_row["meets_threshold"], "False")

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

    def test_fetch_jobs_keeps_individual_job_results_without_model_extraction(self):
        search_wrapper = Mock()
        search_wrapper.results.return_value = [
            {
                "title": "Data Analyst - Acme",
                "link": "https://example.com/job",
                "snippet": "Acme analyst role. Analyze data.",
            },
            {
                "title": "Duplicate result",
                "link": "https://example.com/job#apply",
                "snippet": "Duplicate.",
            },
        ]
        with (
            patch(
                "langchain_community.utilities.DuckDuckGoSearchAPIWrapper",
                return_value=search_wrapper,
            ) as search_wrapper_class,
            patch.object(agent, "_ollama_generate") as generate,
        ):
            jobs = agent.fetch_jobs(
                ["analyst"],
                "Remote",
                5,
                "healthcare",
            )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].title, "Data Analyst - Acme")
        self.assertEqual(jobs[0].company, "See posting")
        self.assertEqual(jobs[0].location, "Remote")
        self.assertEqual(jobs[0].description, "Acme analyst role. Analyze data.")
        self.assertEqual(jobs[0].url, "https://example.com/job")
        self.assertIsNone(jobs[0].salary_min)
        generate.assert_not_called()
        search_wrapper_class.assert_called_once_with()
        search_wrapper.results.assert_called_once_with(
            "analyst job opening apply Remote healthcare", max_results=5
        )

    def test_fetch_jobs_discards_indeed_search_page_but_keeps_direct_job(self):
        search_wrapper = Mock()
        search_wrapper.results.return_value = [
            {
                "title": "$35K-$139K Aerospace Jobs in Canada | Indeed",
                "link": "https://ca.indeed.com/Aerospace-jobs-in-Canada",
                "snippet": "3,507 Aerospace jobs available in Canada. Apply to ... and more!",
            },
            {
                "title": "Avionics Engineer",
                "link": "https://ca.indeed.com/viewjob?jk=abc123",
                "snippet": "Acme is hiring an Avionics Engineer. Apply now.",
            },
            {
                "title": "Search Aerospace jobs",
                "link": "https://www.linkedin.com/jobs/search/?keywords=aerospace",
                "snippet": "Browse aerospace jobs.",
            },
            {
                "title": "Avionics Engineer - Acme Careers",
                "link": "https://careers.acme.example/jobs/avionics-engineer-123",
                "snippet": "Apply for the Avionics Engineer role.",
            },
            {
                "title": "Systems Engineer - Contoso",
                "link": "https://careers.contoso.example/opening/8f12",
                "snippet": "Contoso is hiring a Systems Engineer.",
            },
            {
                "title": "Engineer jobs",
                "link": "https://careers.contoso.example/search?query=engineer",
                "snippet": "Browse open Engineer roles.",
            },
            {
                "title": "Aerospace jobs in Canada",
                "link": "https://jobs.board.example/jobs-in-canada",
                "snippet": "Browse 3,507 aerospace jobs in Canada.",
            },
        ]
        with patch(
            "langchain_community.utilities.DuckDuckGoSearchAPIWrapper",
            return_value=search_wrapper,
        ):
            jobs = agent.fetch_jobs(["aerospace engineer"], "Canada", 10)
        self.assertEqual(
            [job.url for job in jobs],
            [
                "https://ca.indeed.com/viewjob?jk=abc123",
                "https://careers.acme.example/jobs/avionics-engineer-123",
                "https://careers.contoso.example/opening/8f12",
            ],
        )

    def test_fetch_jobs_returns_empty_when_search_has_no_results(self):
        with (
            patch(
                "langchain_community.utilities.DuckDuckGoSearchAPIWrapper"
            ) as search_wrapper_class,
        ):
            search_wrapper_class.return_value.results.return_value = []
            jobs = agent.fetch_jobs(
                ["analyst"], "Remote", 5
            )
        self.assertEqual(jobs, [])

    def test_fetch_jobs_any_industry_does_not_add_sector_filter(self):
        search_wrapper = Mock()
        search_wrapper.results.return_value = [
            {
                "title": "Data Analyst",
                "link": "https://careers.example.com/job/data-analyst-1",
                "snippet": "Analyze data across business teams.",
            }
        ]
        with patch(
            "langchain_community.utilities.DuckDuckGoSearchAPIWrapper",
            return_value=search_wrapper,
        ):
            agent.fetch_jobs(["data analyst"], "Remote", 5, "any")
        search_wrapper.results.assert_called_once_with(
            "data analyst job opening apply Remote", max_results=5
        )

    def test_assess_posting_includes_target_industry(self):
        posting = agent.Posting("1", "Analyst", "Acme", "Remote", "Analyze data", "https://example.com")
        response = {
            "is_individual_posting": True,
            "industry_match": True,
            "requirements": [
                {
                    "criterion": "Analysis",
                    "priority": "preferred",
                    "status": "met",
                    "evidence": "Analyzed data",
                }
            ],
            "summary": "Role aligns with experience",
        }
        with patch.object(
            agent, "_ollama_generate", return_value=json.dumps(response)
        ) as generate:
            agent.assess_posting(
                "resume", posting, "llama3.2:3b", "http://localhost:11434", "healthcare"
            )
        self.assertIn("The target industry is healthcare", generate.call_args.args[0])
        self.assertIn("do not treat search/category pages", generate.call_args.args[0])
        self.assertIn("https://example.com", generate.call_args.args[0])

    def test_assess_posting_calculates_weighted_requirements_score(self):
        posting = agent.Posting(
            "1", "Analyst", "Acme", "Remote", "SQL and reporting", "https://example.com/job"
        )
        response = {
            "is_individual_posting": True,
            "industry_match": True,
            "requirements": [
                {
                    "criterion": "SQL",
                    "priority": "required",
                    "status": "met",
                    "evidence": "Used SQL daily",
                },
                {
                    "criterion": "Two years of analytics",
                    "priority": "required",
                    "status": "missing",
                    "evidence": "",
                },
                {
                    "criterion": "Dashboard experience",
                    "priority": "preferred",
                    "status": "met",
                    "evidence": "Built dashboards",
                },
            ],
            "summary": "SQL evidence is present, but the experience requirement is not.",
        }
        with patch.object(
            agent, "_ollama_generate", return_value=json.dumps(response)
        ) as generate:
            assessment = agent.assess_posting(
                "Used SQL daily and built dashboards",
                posting,
                "llama3.2:3b",
                "http://localhost:11434",
                "any",
            )

        self.assertEqual(assessment.required_score, 50)
        self.assertEqual(assessment.preferred_score, 100)
        self.assertEqual(assessment.score, 65)
        self.assertFalse(assessment.qualified)
        self.assertEqual(assessment.matching_skills, ["SQL", "Dashboard experience"])
        self.assertEqual(assessment.gaps, ["Two years of analytics"])
        self.assertIn("Python calculates it", generate.call_args.args[0])

    def test_assess_posting_has_no_score_without_assessable_requirements(self):
        posting = agent.Posting(
            "1", "Analyst", "Acme", "Remote", "", "https://example.com/job"
        )
        response = {
            "is_individual_posting": True,
            "industry_match": True,
            "requirements": [],
            "summary": "The snippet is too short.",
        }
        with patch.object(
            agent, "_ollama_generate", return_value=json.dumps(response)
        ):
            assessment = agent.assess_posting(
                "resume", posting, "llama3.2:3b", "http://localhost:11434"
            )
        self.assertIsNone(assessment.score)
        self.assertFalse(assessment.qualified)
        self.assertIn("not contain enough assessable requirements", assessment.reason)

    def test_assess_posting_any_industry_does_not_penalize_sector(self):
        posting = agent.Posting(
            "1", "Analyst", "Acme", "Remote", "Analyze data", "https://example.com"
        )
        response = {
            "is_individual_posting": True,
            "industry_match": True,
            "requirements": [
                {
                    "criterion": "Analysis",
                    "priority": "preferred",
                    "status": "met",
                    "evidence": "Analyzed data",
                }
            ],
            "summary": "Role aligns with experience",
        }
        with patch.object(
            agent, "_ollama_generate", return_value=json.dumps(response)
        ) as generate:
            agent.assess_posting(
                "resume", posting, "llama3.2:3b", "http://localhost:11434", "any"
            )
        prompt = generate.call_args.args[0]
        self.assertIn("candidate is open to any industry", prompt)
        self.assertIn("Do not penalize or reject a posting based on its industry", prompt)

    def test_generate_tailored_resumes_creates_docs_for_qualified_jobs_only(self):
        qualified = agent.Assessment(
            agent.Posting(
                "1",
                "Data Analyst",
                "Acme",
                "Remote",
                "Analyze datasets and communicate findings.",
                "https://example.com/jobs/analyst",
            ),
            85,
            True,
            "Strong match",
            ["SQL"],
            [],
        )
        below_threshold = agent.Assessment(
            agent.Posting(
                "2",
                "Engineer",
                "Other",
                "Remote",
                "Engineering role",
                "https://example.org/jobs/engineer",
            ),
            95,
            False,
            "Not qualified",
            [],
            [],
        )
        tailored_content = "# Summary\nExperienced analyst.\n\n# Skills\n- SQL"
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "tailored"
            with patch.object(
                agent,
                "_ollama_generate",
                return_value=json.dumps({"tailored_resume": tailored_content}),
            ) as generate:
                outputs = agent.generate_tailored_resumes(
                    "Source resume: analyst with SQL",
                    [qualified, below_threshold],
                    output_dir,
                    60,
                    "llama3.2:3b",
                    "http://localhost:11434",
                    "any",
                )

            self.assertEqual(len(outputs), 1)
            self.assertEqual(len(list(output_dir.glob("*.docx"))), 1)
            document = Document(outputs[0])
            content = "\n".join(paragraph.text for paragraph in document.paragraphs)
            self.assertIn("Tailored Resume Draft", content)
            self.assertIn("AI-assisted draft", content)
            self.assertIn("Target: Data Analyst | Acme", content)
            self.assertIn("Experienced analyst.", content)
            self.assertEqual(generate.call_count, 1)
            prompt = generate.call_args.args[0]
            self.assertIn("do not add or imply experience", prompt)
            self.assertIn("https://example.com/jobs/analyst", prompt)

    def test_generate_tailored_resumes_does_not_create_empty_output_dir(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "tailored"
            self.assertEqual(
                agent.generate_tailored_resumes(
                    "resume",
                    [],
                    output_dir,
                    60,
                    "llama3.2:3b",
                    "http://localhost:11434",
                ),
                [],
            )
            self.assertFalse(output_dir.exists())

    def test_run_needs_no_external_search_api_key(self):
        with tempfile.TemporaryDirectory() as directory:
            resume = Path(directory) / "resume.txt"
            resume.write_text("Resume", encoding="utf-8")
            output_docx = Path(directory) / "jobs.docx"
            output_csv = Path(directory) / "jobs.csv"
            args = Namespace(
                resume=str(resume),
                max_results=15,
                min_score=60,
                model="llama3.2:3b",
                ollama_host="http://localhost:11434",
                location="Remote",
                output=str(output_docx),
                csv_output=str(output_csv),
            )
            with patch.dict("os.environ", {}, clear=True):
                with (
                    patch.object(agent, "extract_search_queries", return_value=["analyst"]),
                    patch.object(agent, "fetch_jobs", return_value=[]),
                    patch.object(agent, "verify_posting_links", return_value=[]),
                ):
                    self.assertEqual(agent.run(args), 0)
            self.assertTrue(output_docx.is_file())
            self.assertTrue(output_csv.is_file())

    def test_parser_exposes_opt_in_resume_tailoring(self):
        args = agent.build_parser().parse_args(
            ["--resume", "resume.txt", "--tailor-resumes"]
        )
        self.assertTrue(args.tailor_resumes)
        self.assertEqual(args.tailored_output_dir, "tailored_resumes")

    def test_ollama_request_uses_local_generate_endpoint_and_json_mode(self):
        payload = {"response": '{"ok": true}'}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                return json.dumps(payload).encode()

        with patch.object(agent, "urlopen", return_value=FakeResponse()) as urlopen:
            result = agent._ollama_generate(
                "return JSON", "qwen2.5:3b", "http://localhost:11434"
            )
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://localhost:11434/api/generate")
        request_data = json.loads(request.data)
        self.assertEqual(request_data["model"], "qwen2.5:3b")
        self.assertEqual(request_data["prompt"], "return JSON")
        self.assertEqual(request_data["format"], "json")
        self.assertFalse(request_data["stream"])
        self.assertEqual(result, '{"ok": true}')


if __name__ == "__main__":
    unittest.main()
