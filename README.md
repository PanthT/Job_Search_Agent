# Resume-Based Job Search Agent

This command-line tool searches for job leads using LangChain's DuckDuckGo integration,
matches them against your resume using a local Ollama model, verifies result links, and writes
Word (`.docx`) and CSV reports. Your resume is sent only to your local Ollama server.
Search terms go to DuckDuckGo; no search API key or OpenRouter/Adzuna account is required.

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com/) installed and running locally
- Internet access for DuckDuckGo search

## Setup

1. Install dependencies:

   ```sh
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Install a local model. The default is Llama 3.2 3B:

   ```sh
   ollama pull llama3.2:3b
   ollama serve
   ```

   Or use a Qwen model instead:

   ```sh
   ollama pull qwen2.5:3b
   ```

3. Save your resume as UTF-8 plain text (`.txt`), PDF (`.pdf`), or Word (`.docx`).

## Run

```sh
python job_search_agent.py --resume ./resume.pdf --location "New York, NY"
```

The default outputs are `qualified_jobs.docx` and `qualified_jobs.csv`. Both save all
discovered individual-job results, including lower-scoring and unqualified results; they
are marked with their fit score and qualification status. The default qualification
threshold is 60/100. Customize the run:

```sh
python job_search_agent.py \
  --resume ./resume.txt \
  --location "Remote" \
  --industry healthcare \
  --model qwen2.5:3b \
  --max-results 20 \
  --min-score 70 \
  --output ./results/jobs.docx \
  --csv-output ./results/jobs.csv
```

`--industry` is optional. It is used in search and in the model's fit assessment.
Pass `--industry any` to search across industries without penalizing jobs for being
outside a particular sector:

```sh
python job_search_agent.py --resume ./resume.pdf --industry any
```

The default model is `llama3.2:3b`; set `--model` or `OLLAMA_MODEL` to a model you've
downloaded in Ollama, such as `qwen2.5:3b`. Use `--ollama-host` or `OLLAMA_HOST` if
Ollama is listening at a non-default local URL.

To generate a separate tailored resume draft for each job marked qualified and meeting
the minimum score, opt in with `--tailor-resumes`. Drafts are saved as DOCX files in
`tailored_resumes/` by default; set `--tailored-output-dir` to choose another directory:

```sh
python job_search_agent.py \
  --resume ./resume.pdf \
  --industry any \
  --tailor-resumes \
  --tailored-output-dir ./results/tailored
```

Tailoring uses the selected local Ollama model and the full source resume plus each
qualified posting. The prompt instructs the model not to add unsupported facts, skills,
credentials, employers, dates, or achievements. Each output is clearly marked as an
AI-assisted draft; review every statement for accuracy before using or submitting it.
The original resume is not modified.

The CSV includes the posting title, company (when available), location, reported salary,
ATS-style `fit_score`, required/preferred coverage, qualification status, fit
explanation, matching skills, gaps, description, source URL, an `apply_url` to the
individual posting page, and link-check status/final URL. The Word report also includes
a clickable link to each individual posting page.
These links lead to the job page where the employer's Apply action is available; the
agent does not extract a separate application-form URL. The agent checks each posting
with an HTTP GET request and follows redirects. A reachable link does not guarantee that
the job is still open or that its page is genuine.

## Search limits and privacy

DuckDuckGo search is accessed through LangChain's community integration and does not
require an API key. The `langchain-community` package is marked as being sunset by
LangChain, so this integration may eventually need migration. Search can be throttled
or change without notice; it is not a guaranteed job-listings API. Search-result and
category pages are excluded; the agent retains URLs that appear to lead to an individual
posting, including individual listings hosted by job boards. Search results are kept as
leads using their original title, snippet, and URL. Employer and salary details may be
unavailable in snippets; review the linked page to confirm them. Link verification
visits the individual posting URLs directly. URL filtering is heuristic, so review links
before applying.

## ATS-style match score

There is no single ATS score shared by modern applicant-tracking systems. Vendors
document different capabilities, such as resume parsing, employer-configured screening
rules, skills search, and interview scorecards; employer-specific ranking formulas are
not generally exposed to this tool. This project therefore calculates its own
transparent ATS-inspired score, not an actual Greenhouse, Workday, LinkedIn, or other
vendor score.

For example, Greenhouse documents resume parsing and employer-configured auto-reject
rules based on application answers; its five-point scorecard ratings are interview
feedback, not a published candidate-to-job score formula. LinkedIn Recruiter and
Workday describe search/skills capabilities without publishing a universal weighted
match score in the referenced product documentation.

The local model identifies distinct requirements from the available search-result
description and marks each as required or preferred and as met, partial, or missing,
with evidence from the resume. Python then calculates coverage: met = 100%, partial =
50%, missing = 0%. When both requirement groups exist, the score is 70% required
coverage and 30% preferred coverage; if only one group exists, that group supplies the
full score. No score is shown if the available description has no assessable criteria.
A result is marked qualified only when it is an individual posting, it matches a
specified industry (if any), and every stated required criterion is fully supported.
The `--min-score` threshold is applied in addition to that qualification check.

The score can only reflect requirements present in the search-result text (up to 5,000
characters), not hidden screening questions or the full employer ATS configuration.
Treat it as a comparison aid, not a guarantee of eligibility or hiring. Review the
original posting and confirm every requirement before applying. API keys are read from
environment variables; do not commit them.

Examples of the distinct vendor capabilities described in public documentation:
[Greenhouse resume parsing](https://support.greenhouse.io/hc/en-us/articles/200989175-Unsuccessful-resume-parse),
[Greenhouse application rules](https://support.greenhouse.io/hc/en-us/articles/203105595-Application-rules-overview),
[Greenhouse interviewer scorecards](https://support.greenhouse.io/hc/en-us/articles/15756249510427-Scorecards-FAQ),
[LinkedIn Recruiter](https://business.linkedin.com/hire/recruiter), and
[Workday Talent Management](https://www.workday.com/en-us/products/human-capital-management/talent-management.html).
These public product descriptions do not establish a shared ATS match-score formula.
