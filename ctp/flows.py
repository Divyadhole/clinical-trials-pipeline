"""Prefect flows.

Thin on purpose. The logic lives in ctp.jobs; this module contributes retries,
task-level logging and a run graph that shows up in the Prefect UI. Scheduling
is done by GitHub Actions, which is the part that actually runs for free with
the laptop closed.
"""

import datetime as dt

from prefect import flow, get_run_logger, task

from . import db, ingest, status, transform
from .config import Config
from .jobs import STREAM, QualityGateFailed
from .quality import run_checks


@task(retries=3, retry_delay_seconds=[30, 120, 300], log_prints=True)
def ingest_task(cfg, run_id, window_start, window_end):
    with db.connect(cfg.database_url) as conn:
        return ingest.load_window(conn, cfg, run_id, window_start, window_end)


@task(retries=1, retry_delay_seconds=30)
def transform_task(cfg, run_id):
    with db.connect(cfg.database_url) as conn:
        staging = transform.build_staging(conn, run_id)
        marts = transform.build_marts(conn, run_id)
    return {**staging, **marts}


@task
def quality_task(cfg, run_id):
    with db.connect(cfg.database_url) as conn:
        outcome = run_checks(conn, run_id)
        db.record_metrics(conn, run_id, outcome["summary"])
    return outcome


@task
def publish_status_task(cfg):
    with db.connect(cfg.database_url) as conn:
        return str(status.write(conn))


@flow(name="clinical-trials-daily")
def daily_flow(today: str = None):
    log = get_run_logger()
    cfg = Config.from_env()
    day = dt.date.fromisoformat(today) if today else dt.date.today()

    with db.connect(cfg.database_url) as conn:
        db.migrate(conn)
        watermark = db.get_watermark(conn, STREAM)
        window_start = watermark or (day - dt.timedelta(days=cfg.lookback_days))
        run_id = db.start_run(conn, "daily", window_start, day)

    log.info("run %s: window %s to %s", run_id, window_start, day)

    try:
        ingest_task(cfg, run_id, window_start, day)
        transform_task(cfg, run_id)
        outcome = quality_task(cfg, run_id)
    except Exception as exc:
        with db.connect(cfg.database_url) as conn:
            db.finish_run(conn, run_id, "failed", str(exc))
        publish_status_task(cfg)
        raise

    blocking = outcome["blocking_failures"]
    with db.connect(cfg.database_url) as conn:
        if blocking:
            names = ", ".join(f["check_name"] for f in blocking)
            db.finish_run(conn, run_id, "failed", f"quality gate failed: {names}")
        else:
            db.set_watermark(conn, STREAM, day)
            db.finish_run(conn, run_id, "succeeded")

    publish_status_task(cfg)

    if blocking:
        raise QualityGateFailed(", ".join(f["check_name"] for f in blocking))
    return {"run_id": run_id, **outcome["summary"]}


if __name__ == "__main__":
    daily_flow()
