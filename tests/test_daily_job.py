"""End-to-end exercise of the daily job with the network replaced.

These assert the two behaviours the whole design hangs on:

  1. Running the same day twice adds nothing.
  2. A blocking quality failure marks the run failed and leaves the watermark
     where it was, so tomorrow re-reads the window instead of skipping it.

The API is monkeypatched rather than called, so a failure here means our logic
broke, not that ClinicalTrials.gov had a slow morning.
"""

import datetime as dt
import os

import pytest

from ctp import api, db, jobs
from ctp.config import Config


@pytest.fixture
def cfg():
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set; skipping integration test")
    return Config(database_url=url, lookback_days=2, requests_per_minute=6000,
                  page_size=1000, max_retries=1)


def _fake_iter(payloads):
    def _iter(self, window_start, window_end, count_total=True):
        yield from payloads
    return _iter


def _count(conn, table):
    with conn.cursor() as cur:
        cur.execute(f"select count(*) from {table}")
        return cur.fetchone()[0]


def test_daily_advances_the_watermark_and_repeats_cleanly(db_conn, cfg, monkeypatch,
                                                          make_study):
    today = dt.date.today()
    yesterday = (today - dt.timedelta(days=1)).isoformat()
    payloads = [make_study(nct_id=f"NCT{i:08d}", last_update=yesterday)
                for i in range(1, 26)]
    monkeypatch.setattr(api.ClinicalTrialsClient, "iter_studies", _fake_iter(payloads))

    first = jobs.run_daily(cfg, today=today)

    assert db.get_watermark(db_conn, jobs.STREAM) == today
    with db_conn.cursor() as cur:
        cur.execute("select status from ops.pipeline_runs where run_id = %s",
                    (first["run_id"],))
        assert cur.fetchone()[0] == "succeeded"

    before = (_count(db_conn, "raw.studies"),
              _count(db_conn, "raw.studies_history"),
              _count(db_conn, "marts.fct_trials"))
    assert before == (25, 25, 25)

    second = jobs.run_daily(cfg, today=today)

    after = (_count(db_conn, "raw.studies"),
             _count(db_conn, "raw.studies_history"),
             _count(db_conn, "marts.fct_trials"))
    assert after == before, "a repeated day must not add rows anywhere"

    with db_conn.cursor() as cur:
        cur.execute(
            "select value from ops.run_metrics "
            " where run_id = %s and metric = 'studies_unchanged'",
            (second["run_id"],),
        )
        assert cur.fetchone()[0] == 25


def test_a_blocking_check_failure_holds_the_watermark(db_conn, cfg, monkeypatch,
                                                      make_study):
    today = dt.date.today()
    yesterday = (today - dt.timedelta(days=1)).isoformat()
    monkeypatch.setattr(
        api.ClinicalTrialsClient, "iter_studies",
        _fake_iter([make_study(nct_id=f"NCT{i:08d}", last_update=yesterday)
                    for i in range(1, 6)]),
    )

    jobs.run_daily(cfg, today=today)
    stale = today - dt.timedelta(days=5)
    db.set_watermark(db_conn, jobs.STREAM, stale)

    real_process = jobs._process_and_check

    def sabotage(conn, cfg_, run_id, window_start, window_end):
        """Break cross-layer parity behind the transform's back, then re-check."""
        real_process(conn, cfg_, run_id, window_start, window_end)
        with conn.cursor() as cur:
            cur.execute("delete from staging.studies where nct_id = 'NCT00000002'")
        conn.commit()
        from ctp.quality import run_checks
        return run_checks(conn, run_id)

    monkeypatch.setattr(jobs, "_process_and_check", sabotage)

    with pytest.raises(jobs.QualityGateFailed):
        jobs.run_daily(cfg, today=today)

    assert db.get_watermark(db_conn, jobs.STREAM) == stale, \
        "a blocking quality failure must not advance the watermark"

    with db_conn.cursor() as cur:
        cur.execute("select status, error from ops.pipeline_runs "
                    " order by run_id desc limit 1")
        status, error = cur.fetchone()
    assert status == "failed"
    assert "quality gate failed" in error
