# Resume-Based Job Search Agent

This command-line tool searches for job leads using LangChain's DuckDuckGo integration,
matches them against your resume using a local Ollama model, verifies result links, and writes
Word (`.docx`) and CSV reports. Your resume is sent only to your local Ollama server.
Search terms go to DuckDuckGo; no search API key or OpenRouter/Adzuna account is required.

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com/) installed locally
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
   ```

   Or use a Qwen model instead:

   ```sh
   ollama pull qwen2.5:3b
   ```

   When you run the agent, it checks whether the local Ollama API is responding. If it
   is not, the agent starts `ollama serve` automatically and waits for the API before
   continuing. Keep Ollama installed and ensure the `ollama` command is available in
   the same terminal environment. You can also start Ollama manually before the agent.

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

Use `--location` at the geographic level you want: country only, province/state and
country, or city, province/state, and country. Every component you provide must match;
for example, a Toronto search excludes Ottawa even though both cities are in Ontario.

```sh
# Country-wide search
python job_search_agent.py --resume ./resume.pdf --location "Canada"

# Province/state-wide search
python job_search_agent.py --resume ./resume.pdf --location "Ontario, Canada"

# City-specific search
python job_search_agent.py \
  --resume ./resume.pdf \
  --location "Toronto, Ontario, Canada"
```

The requested `--location` is not treated as proof of a posting's actual location. The
agent extracts the location stated in each posting and compares each requested
component. Explicitly mismatched postings are excluded from both reports; when the
source does not state enough location detail, the report labels it `Not verified`
rather than copying the search location into the job's location field. Remote jobs are
included only when the posting says they are eligible in the requested area.

Searches include the broader web, a targeted Indeed query for individual posting pages,
and a startup/small-business query. The Indeed query uses the resume-derived role terms,
location, and industry (if set); broad Indeed search/listing pages are still excluded.
When results are available, roughly one-third of `--max-results` is reserved for Indeed
postings and another third for startup/small-business leads; unused slots are filled from
the other searches.

The agent does not start from a fixed list of SMB employers. It searches for roles based
on the resume-derived job queries, then looks for startup/small-business signals in the
search results. Once an employer is identified, it searches for explicit employee-count
and current-hiring evidence. To focus on SMB opportunities in a specific industry, use
`--smb-focus` together with `--industry` (`--smb-only` remains an alias):

```sh
python job_search_agent.py \
  --resume ./resume.pdf \
  --location Canada \
  --industry aerospace \
  --smb-focus
```

In this mode, the targeted search combines resume-matched job queries with the selected
industry and SMB/startup terms, and searches direct posting URLs on Lever and Greenhouse
(plus Canada's Job Bank when the location is Canada). Search diagnostics report raw
results, individual posting URLs retained, and links filtered as non-postings. Explicit
evidence of 1-500 employees marks an employer as a verified SMB. Postings whose employer
size cannot be verified are retained as unverified candidates, so search runs don't
silently become empty solely because employee-count sources are missing. Employers known
to have more than 500 employees are excluded. The company-size status and any evidence
source are included in the CSV/report. This is a conservative discovery aid, not a
guarantee that search-engine results cover every SMB or that every size source is current.

The default model is `llama3.2:3b`; set `--model` or `OLLAMA_MODEL` to a model you've
downloaded in Ollama, such as `qwen2.5:3b`. Use `--ollama-host` or `OLLAMA_HOST` if
Ollama is listening at a non-default local URL. If Ollama returns an assessment that
does not match the required JSON fields, the agent retries that assessment once; if the
retry is still invalid, it reports the missing or malformed field names.
Each local generation request has a 10-minute timeout by default to allow for a cold
model load or a slower machine. If a request still times out, retry with a longer
timeout, for example:

```sh
OLLAMA_GENERATE_TIMEOUT=900 python job_search_agent.py \
  --resume ./resume.pdf \
  --location Canada \
  --industry aerospace \
  --smb-focus
```

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

For each posting with an identified employer, the agent also searches the web for an
explicit employee-count statement and current individual openings. Reports include
employee-count evidence and its source URL, plus any individual opening found in the
employer-targeted hiring search and its source. Missing evidence is labeled
`Not verified`; the agent does not infer company size from startup/SMB search terms. A
search result is a signal, not proof that a job remains open; confirm details on the
cited source or employer site. If the employer cannot be identified from the job result,
those facts remain unverified.

The CSV includes the posting title, company (when available), reported employee count
and source, hiring signal and source, location, reported salary,
ATS-style `fit_score`, required/preferred coverage, qualification status, fit
explanation, matching skills, gaps, description, source URL, an `apply_url` to the
individual posting page, and link-check status/final URL. The Word report also includes
a clickable link to each individual posting page.
These links lead to the job page where the employer's Apply action is available; the
agent does not extract a separate application-form URL. The agent checks each posting
with an HTTP GET request and follows redirects. A reachable link does not guarantee that
the job is still open or that its page is genuine.

## Search limits and privacy

Web search is accessed through LangChain's community integration and does not require
an API key. It uses selected search engines instead of `ddgs`'s automatic mode, which
can query unrelated engines such as Wikipedia and fail when those hosts are unavailable.
If an individual query produces no results, the agent continues through the remaining
queries. If all configured backends fail due to network/DNS issues, it reports the
connection error rather than treating it as an empty search. The `langchain-community`
package is marked as being sunset by
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
