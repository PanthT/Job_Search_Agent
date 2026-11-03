# Corporate website repair

No OpenAI API key is needed. Uses the existing ddgs dependency.

From this repository with its virtual environment activated:

```sh
python repair_company_homepages.py --workers 4
```

Only blank homepage cells are searched. Results are checkpointed atomically after
each company, with a timestamped CSV backup before changes. Existing links and
unrelated columns are preserved. Run the command again to resume unchecked rows.
Use `--limit 20` for a small batch, or `--retry-checked` to revisit unresolved rows.

`homepage_search_error` records lookup failures without overwriting errors from
other pipeline steps. `homepage_checked_at` records when discovery ran;
`homepage_source` records the verification method. Domain-only or inaccessible
matches are kept in `homepage_candidate` for review, not promoted to corporate sites.
Matching page content is evidence, not a guarantee of company ownership. Ambiguous
brands and renamed organizations still require manual review.

The company finder now checks homepage title identity for abbreviated domains and
excludes additional commercial company directories. The CSV builder preserves a
repaired link's verification when the source CSV has no replacement website.

Automatic discoveries have `homepage_review_status` = `Needs ownership review`:
a page repeating a company name cannot prove ownership. Domains containing a
hyphenated legal suffix are held as candidates for manual review. Manually checked
sites are marked separately. Keep this distinction when contacting companies.
