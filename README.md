# clinical-trials-pipeline

A daily incremental pipeline that pulls study records from the
[ClinicalTrials.gov v2 API](https://clinicaltrials.gov/data-api/api) into
Postgres, models them in three layers, tests them, and publishes a status page.

**[Live status page](https://divyadhole.github.io/clinical-trials-pipeline/)** —
last successful run, row counts per run, and the pass/fail history of every data
quality check.
**[INCIDENTS.md](INCIDENTS.md)** — every time it has broken and what changed.

---

## Run it

```bash
git clone https://github.com/Divyadhole/clinical-trials-pipeline.git
cd clinical-trials-pipeline
docker compose up
```

That is the whole setup. No account, no API key, no manual SQL. Compose starts
Postgres, applies the migrations, loads the last two days of updated studies,
runs the quality suite, and writes `docs/index.html`.

To run it against your own database instead:

```bash
cp .env.example .env          # point DATABASE_URL wherever you like
pip install -r requirements.txt
python -m ctp verify-api      # confirm the live API contract first
python -m ctp daily
python -m ctp status
```

---

## Why this source

Three properties, all of which the pipeline has to handle rather than avoid:

- **It changes underneath you.** Studies are revised after registration, so a
  record loaded in week one legitimately reappears in week six with different
  content. That makes `LastUpdatePostDate` the incremental key and makes
  idempotency a real requirement rather than a talking point.
- **It is cursor-paginated and rate limited.** Roughly 50 requests per minute
  per IP, enforced with 429s. Pagination is by opaque `nextPageToken`, so you
  cannot skip ahead or resume a page you did not finish.
- **It mixes clean structured fields with long free text.** The eligibility
  criteria are unstructured prose, which is what
  [eligibility-criteria-extraction](https://github.com/Divyadhole/eligibility-criteria-extraction)
  is built on top of.

---

## How it is put together

| Layer | What lives there | Grain |
|---|---|---|
| `raw.studies` | the API payload as received, current version | one row per study |
| `raw.studies_history` | every distinct version ever seen | one row per (study, content hash) |
| `staging.studies` | typed and flattened, plus condition and intervention child tables | one row per study |
| `marts.fct_trials` | fact table with `dim_sponsor` and a condition bridge | one row per study |
| `ops.*` | run log, per-run metrics, quality results, watermarks, schema fingerprints | one row per run / check |

Nothing is interpreted in `raw`. If a downstream assumption turns out wrong,
the payload is still there and the whole thing can be rebuilt without
re-crawling the API.

### Idempotency

Re-running yesterday does not duplicate yesterday. Each study is hashed; the
load upserts on `nct_id` and writes to history only when the hash is one it has
not seen for that study. Consecutive daily windows deliberately overlap by one
day so a study posted late in the day cannot fall through the gap — the overlap
is free precisely because the load is an upsert.

This is asserted, not asserted-at:
`tests/test_idempotency.py::test_reloading_the_same_window_changes_nothing`
loads five studies, loads the same five again, and fails if any table grew.

### Backfill

```bash
python -m ctp backfill --start 2026-01-01 --end 2026-03-31 --chunk-days 7
```

Chunked, throttled, and resumable: progress is stored as its own watermark, so
an interrupted backfill restarts at the chunk boundary rather than at the
beginning. Running a completed backfill again is a no-op.

### Data quality

Eighteen checks across five families — freshness, cross-layer completeness,
null budgets, uniqueness, referential integrity, plus accepted values, schema
drift and a volume anomaly check. Every result is written to `ops.test_results`,
so the status page shows a pass rate per check over time rather than a green
tick for today.

`error` severity fails the run **and holds the watermark back**, so a bad day
gets re-read tomorrow instead of being skipped. `warn` is recorded and visible
but does not stop the pipeline.

Two of these are the ones that matter:

- **schema drift** compares the set of JSON paths seen this run against last
  run. This is the failure that otherwise produces silent nulls for weeks.
- **volume anomaly** compares this run's fetch count against the trailing median
  of the last seven. It catches the case where the API returns `200` with an
  empty result set and the pipeline reports a cheerful success having loaded
  nothing.

The suite is proven able to fail:
`tests/test_idempotency.py::test_a_broken_invariant_actually_fails_a_check`
deletes a staging row behind the transform's back and asserts that
`raw_staging_parity` catches it.

---

## Scheduling

A Prefect flow (`ctp/flows.py`) provides the task graph, retries and logging.
GitHub Actions runs it daily at 09:17 UTC against a hosted Postgres, commits the
regenerated status page, and needs nothing running on my laptop.

Prefect for the flow semantics, Actions for the schedule, because there is no
genuinely free hosted Airflow and pretending otherwise would mean a scheduler
that quietly stops when a trial expires.

**Deploy your own:** set the repository secret `NEON_DATABASE_URL` to a Postgres
connection string and enable GitHub Pages on `/docs`.

---

## Commands

| Command | What it does |
|---|---|
| `python -m ctp verify-api` | one live request; prints the response envelope and asserts the date filter is respected |
| `python -m ctp migrate` | apply SQL migrations (idempotent) |
| `python -m ctp daily` | incremental load from the watermark to today |
| `python -m ctp backfill --start … --end …` | historical range, chunked and resumable |
| `python -m ctp checks` | quality suite only; exits non-zero on an error-severity failure |
| `python -m ctp status` | regenerate `docs/index.html` |

`verify-api` exists because a malformed advanced filter returns `200` with zero
results rather than an error. Before believing a zero-row load, run it.

---

## What this deliberately is not

- **No Spark, Kafka or Snowflake.** The daily delta is thousands of rows.
  Postgres is the right size and the honest answer.
- **No React front end.** The status page is one static HTML file with no
  JavaScript, regenerated by the pipeline and served by GitHub Pages.
- **No vector database.** There is no retrieval problem in this repo.
- **No dbt.** Four SQL files and two transform functions do not need a second
  framework on top of them.

---

## Tests

```bash
pytest                                  # unit tests only
DATABASE_URL=postgresql://... pytest    # adds the integration tests
```

Integration tests skip cleanly when `DATABASE_URL` is unset and run against a
real Postgres service in CI on every push. They use synthetic payloads rather
than live API calls, so a failure means our logic broke, not that
ClinicalTrials.gov changed today.
