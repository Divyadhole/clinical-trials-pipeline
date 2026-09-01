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

None yet. First scheduled run is pending. This section stays empty until the
pipeline has actually failed in production, and it will.

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
