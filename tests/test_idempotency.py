"""The test the whole project exists to be able to pass.

Re-running a window must not duplicate anything. These run against a real
Postgres and are skipped when DATABASE_URL is unset, so `pytest` still works
on a laptop with nothing running.

No network: the loader's write path is exercised directly with synthetic
payloads, so the assertions are about our idempotency and not about whether
ClinicalTrials.gov happened to change today.
"""

import datetime as dt

from ctp import db, ingest, transform
from ctp.api import content_hash
from ctp.extract import last_update_post_date, nct_id
from ctp.quality import run_checks


def _batch(studies):
    return [
        {
            "nct_id": nct_id(s),
            "last_update_post_date": last_update_post_date(s),
            "content_hash": content_hash(s),
            "payload": s,
        }
        for s in studies
    ]


def _counts(conn):
    with conn.cursor() as cur:
        cur.execute("select count(*) from raw.studies")
        raw = cur.fetchone()[0]
        cur.execute("select count(*) from raw.studies_history")
        history = cur.fetchone()[0]
        cur.execute("select count(*) from staging.studies")
        staging = cur.fetchone()[0]
        cur.execute("select count(*) from marts.fct_trials")
        marts = cur.fetchone()[0]
    return raw, history, staging, marts


def _load(conn, run_id, studies):
    counters = {
        "studies_new": 0, "studies_changed": 0, "studies_unchanged": 0,
    }
    ingest._flush(conn, run_id, _batch(studies), counters)
    return counters


def test_reloading_the_same_window_changes_nothing(db_conn, make_study):
    studies = [make_study(nct_id=f"NCT{i:08d}") for i in range(1, 6)]

    run_a = db.start_run(db_conn, "test", dt.date(2026, 8, 1), dt.date(2026, 8, 2))
    first = _load(db_conn, run_a, studies)
    transform.build_staging(db_conn, run_a)
    transform.build_marts(db_conn, run_a)
    before = _counts(db_conn)

    assert first["studies_new"] == 5
    assert before == (5, 5, 5, 5)

    run_b = db.start_run(db_conn, "test", dt.date(2026, 8, 1), dt.date(2026, 8, 2))
    second = _load(db_conn, run_b, studies)
    transform.build_staging(db_conn, run_b)
    transform.build_marts(db_conn, run_b)
    after = _counts(db_conn)

    assert second["studies_unchanged"] == 5
    assert second["studies_new"] == 0
    assert second["studies_changed"] == 0
    assert after == before, "re-running the same window must not add rows"


def test_a_revised_study_updates_in_place_and_gains_one_history_row(db_conn, make_study):
    original = make_study(nct_id="NCT00000042", title="Original title")

    run_a = db.start_run(db_conn, "test")
    _load(db_conn, run_a, [original])
    transform.build_staging(db_conn, run_a)

    revised = make_study(nct_id="NCT00000042", title="Revised title",
                         last_update="2026-08-28")
    run_b = db.start_run(db_conn, "test")
    counters = _load(db_conn, run_b, [revised])
    transform.build_staging(db_conn, run_b)

    assert counters["studies_changed"] == 1

    raw, history, staging, _ = _counts(db_conn)
    assert raw == 1, "the current-state table stays at one row per study"
    assert history == 2, "both versions are kept"
    assert staging == 1

    with db_conn.cursor() as cur:
        cur.execute("select brief_title from staging.studies where nct_id = %s",
                    ("NCT00000042",))
        assert cur.fetchone()[0] == "Revised title"


def test_removed_child_rows_do_not_linger(db_conn, make_study):
    study = make_study(nct_id="NCT00000077")
    run_a = db.start_run(db_conn, "test")
    _load(db_conn, run_a, [study])
    transform.build_staging(db_conn, run_a)

    with db_conn.cursor() as cur:
        cur.execute("select count(*) from staging.study_conditions")
        assert cur.fetchone()[0] == 2

    trimmed = make_study(nct_id="NCT00000077", last_update="2026-08-28")
    trimmed["protocolSection"]["conditionsModule"]["conditions"] = ["Obesity"]
    run_b = db.start_run(db_conn, "test")
    _load(db_conn, run_b, [trimmed])
    transform.build_staging(db_conn, run_b)

    with db_conn.cursor() as cur:
        cur.execute("select condition from staging.study_conditions order by condition")
        assert [r[0] for r in cur.fetchall()] == ["Obesity"]


def test_quality_suite_runs_and_passes_on_clean_data(db_conn, make_study):
    studies = [make_study(nct_id=f"NCT{i:08d}") for i in range(1, 21)]
    run_id = db.start_run(db_conn, "test")
    _load(db_conn, run_id, studies)
    transform.build_staging(db_conn, run_id)
    transform.build_marts(db_conn, run_id)

    outcome = run_checks(db_conn, run_id)
    blocking = [f["check_name"] for f in outcome["blocking_failures"]]

    # freshness is expected to fail on fixture data with a fixed past date, so
    # it is excluded here. Every other error-severity check must pass.
    assert [n for n in blocking if n != "freshness_raw"] == []
    assert outcome["summary"]["checks_run"] >= 15


def test_a_broken_invariant_actually_fails_a_check(db_conn, make_study):
    """A check suite that cannot fail is decoration. Prove this one can."""
    studies = [make_study(nct_id=f"NCT{i:08d}") for i in range(1, 6)]
    run_id = db.start_run(db_conn, "test")
    _load(db_conn, run_id, studies)
    transform.build_staging(db_conn, run_id)
    transform.build_marts(db_conn, run_id)

    # Delete one staging row behind the transform's back: raw and staging now
    # disagree, which is exactly the silent-drop failure raw_staging_parity is
    # there to catch.
    with db_conn.cursor() as cur:
        cur.execute("delete from staging.studies where nct_id = 'NCT00000003'")
    db_conn.commit()

    outcome = run_checks(db_conn, run_id)
    failed = {f["check_name"] for f in outcome["blocking_failures"]}
    assert "raw_staging_parity" in failed
