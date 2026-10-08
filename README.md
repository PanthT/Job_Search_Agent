# Resume-Based Job Search Agent

A command-line tool that searches the live web for job postings related to your resume,
uses OpenRouter to assess fit, and creates Word (`.docx`) and CSV reports. Job discovery
and matching both use OpenRouter; no Adzuna account or API is required. The resume is
sent to OpenRouter for query generation and job assessment.

## Requirements

- Python 3.10+
- An [OpenRouter API key](https://openrouter.ai/keys)

## Setup

1. Install dependencies:

   ```sh
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Set your OpenRouter API key:

   ```sh
   export OPENROUTER_API_KEY="your_openrouter_api_key"
   ```

3. Save your resume as UTF-8 plain text (`.txt`), PDF (`.pdf`), or Word (`.docx`). 
   The tool now supports all three formats directly.

## Run

```sh
python job_search_agent.py --resume ./resume.txt --location "New York, NY"
```

The default outputs are `qualified_jobs.docx` and `qualified_jobs.csv`. Both include
only postings that the model marks qualified and scores at least 60/100. Customize the run:

```sh
python job_search_agent.py \
  --resume ./resume.txt \
  --location "Remote" \
  --industry healthcare \
  --max-results 20 \
  --min-score 70 \
  --output ./results/jobs.docx \
  --csv-output ./results/jobs.csv
```

The CSV contains the title, company, location, reported salary, fit score, qualification
status, fit explanation, matching skills, gaps, description, original posting URL, and
link-check status/final URL. Before assessment and export, the agent visits each posting
URL using an HTTP GET request, follows redirects, and records whether it returned a
successful response. The Word and CSV reports both show this result and the final
redirect destination. Links that cannot be verified are still listed but clearly marked.
This confirms that a URL was reachable at check time; it cannot guarantee the job is
still open or that the page content is a genuine job advertisement.

`--industry` is optional. When supplied, the agent asks the model to generate relevant
search terms, includes the industry in web search, and considers industry alignment when
assessing postings. The report records the selected industry.

Use `--help` to see all options. Each run makes one OpenRouter call to identify search
terms, one web-search-enabled call to find postings, and one call per posting to assess
fit. Select a particular OpenRouter model with `--model` or `OPENROUTER_MODEL`.

The free router selects among models currently available for free, so the specific
model may vary and free usage is subject to OpenRouter's current rate limits and availability.
An API key alone does not make every model free; verify model pricing in the OpenRouter
catalog before selecting a specific model. The originally requested Llama 3.2 3B is
not currently listed as a zero-priced OpenRouter model.
Web search is an OpenRouter server tool (currently documented as beta); its availability,
rate limits, and any charges may differ from the free model's inference pricing. Check
OpenRouter's current web-search and model pricing before running searches.

## Notes

- Fit scores are model-generated screening aids, not guarantees of eligibility or hiring.
  Review each original posting and confirm its requirements before applying.
- Search coverage depends on web-search availability, query generation, and the location supplied.
- API credentials are read from environment variables; do not commit real credentials.
