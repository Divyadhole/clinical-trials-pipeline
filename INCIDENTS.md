# Incidents

Every time this pipeline has broken, what broke, and what changed so it does
not break the same way twice. Newest first.

The point of this file is that a pipeline which has never failed has almost
certainly never run. If this file is empty in six months, do not trust the
pipeline; trust the file.

**Format**

```
## YYYY-MM-DD — one line summary
Detected by:  how we found out (check name, failed run, someone noticed)
Impact:       what was wrong in the data, and for how long
Cause:        the actual cause, not the symptom
Fix:          what changed
Prevention:   the test or check that now catches it
```

---

## Production incidents

## 2026-09-16 — fifteen consecutive scheduled runs failed, and never reached the database
Detected by:  Nobody, for fifteen days. The workflow was red on the Actions tab
              the whole time and there was no alert on it. Found only when the
              run history was opened by hand. That is the worst detail in this
              entry and the one most worth fixing.
Impact:       No data was ingested between the first scheduled run and 2026-09-16.
              No data was corrupted either &mdash; the job never got far enough to
              write anything, so the tables hold a clean but stale snapshot.
              `docs/index.html` was never written, which is why the status page
              linked from the README is a 404.
Cause:        Two unrelated faults, stacked.
              1. `pytest` could not import `ctp`. pytest puts the *test*
                 directory on `sys.path`, not the repo root. Local runs used
                 `python -m pytest`, where the interpreter adds the working
                 directory itself, so the suite passed on the laptop and failed
                 in CI on the bare `pytest` command. The test step failed before
                 the pipeline step ran.
              2. `NEON_DATABASE_URL` was never set as a repository secret, so
                 `DATABASE_URL` was empty. Had the tests passed, the run would
                 have failed anyway, sixty seconds later, as a psycopg2
                 connection error buried under Prefect's traceback.
Fix:          `pythonpath = ["."]` in `pyproject.toml`. Two preflight steps in
              `daily.yml` that run before any install: one asserts
              `DATABASE_URL` is non-empty and names the exact setting to change,
              the other opens a real connection and runs `select 1`. The
              status-page step now exits 0 when `docs/index.html` was not
              written, instead of `git add` exiting 128 and reporting a second
              failure on top of the first.
Prevention:   CI runs the same bare `pytest` command the workflow uses, so the
              import path is exercised on every push rather than only on a
              schedule. The preflight turns a missing secret into one sentence
              at five seconds instead of a stack trace at sixty.
              Still missing: nothing notifies anyone when the schedule fails.
              Fifteen silent failures were possible because no one was watching,
              and no code change in this commit fixes that.

---

## Pre-deployment defects

Kept separately because these were caught by the test suite before anything was
scheduled. They are not incidents, but they are the same kind of evidence.

## 2026-08-29 — staging upsert crashed on every batch
Detected by:  `tests/test_idempotency.py`, first run against a real Postgres
Impact:       None in production; the transform had never run outside unit tests.
Cause:        `execute_values` was given a 20-value tuple against a 19-placeholder
              template. The nineteen staging columns were passed with an extra
              trailing `None` while the template already supplied `now()` for
              `loaded_at`, so psycopg2 raised
              "not all arguments converted during string formatting".
Fix:          Pass the column tuple unmodified and let the template supply
              `now()`.
Prevention:   The integration tests now run against a real Postgres service in
              CI on every push, not only unit tests with the database mocked
              out. A unit test would never have caught this: the mismatch only
              exists at the point psycopg2 renders the SQL.
