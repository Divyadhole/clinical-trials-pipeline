"""The jobs themselves, with no orchestrator in sight.

Prefect calls these. So do the CLI and the tests. Keeping the orchestrator out
of this module is what makes it possible to run the whole pipeline locally with
one command and to swap schedulers later without touching the logic.
"""

import datetime as dt
import logging

from . import db, ingest, transform
from .config import Config
from .quality import run_checks

LOG = logging.getLogger(__name__)

STREAM = "studies_last_update"


class QualityGateFailed(RuntimeError):
    pass


def _process_and_check(conn, cfg, run_id, window_start, window_end):
    ingest.load_window(conn, cfg, run_id, window_start, window_end)
    transform.build_staging(conn, run_id)
    transform.build_marts(conn, run_id)
    outcome = run_checks(conn, run_id)
    db.record_metrics(conn, run_id, outcome["summary"])
    return outcome


def run_daily(cfg: Config = None, today: dt.date = None) -> dict:
    """Incremental load from the watermark to today.

    The window starts at the stored watermark rather than the day after it, so
    consecutive runs overlap by one day. Records posted late in the day would
    otherwise fall in the gap. The overlap costs nothing because the load is an
    upsert.
    """
    cfg = cfg or Config.from_env()
    today = today or dt.date.today()

    with db.connect(cfg.database_url) as conn:
        db.migrate(conn)

        watermark = db.get_watermark(conn, STREAM)
        window_start = watermark or (today - dt.timedelta(days=cfg.lookback_days))
        window_end = today

        run_id = db.start_run(conn, "daily", window_start, window_end)
        LOG.info("run %d: daily window %s to %s", run_id, window_start, window_end)

        try:
            outcome = _process_and_check(conn, cfg, run_id, window_start, window_end)
        except Exception as exc:
            db.finish_run(conn, run_id, "failed", str(exc))
            raise

        if outcome["blocking_failures"]:
            names = ", ".join(f["check_name"] for f in outcome["blocking_failures"])
            db.finish_run(conn, run_id, "failed", f"quality gate failed: {names}")
            # The watermark deliberately does not advance. Tomorrow's run will
            # re-read this window, which is safe and is the point of idempotency.
            raise QualityGateFailed(names)

        db.set_watermark(conn, STREAM, window_end)
        db.finish_run(conn, run_id, "succeeded")
        return {"run_id": run_id, **outcome["summary"]}


def run_backfill(cfg: Config = None, start: dt.date = None, end: dt.date = None,
                 chunk_days: int = 7) -> dict:
    """Load a historical date range in chunks, resumable and idempotent.

    Resumability is stored as its own watermark so an interrupted backfill picks
    up at the chunk boundary instead of starting over. Re-running a completed
    backfill is a no-op against raw.studies_history.
    """
    cfg = cfg or Config.from_env()
    if start is None or end is None:
        raise ValueError("backfill needs an explicit start and end date")
    if start > end:
        raise ValueError("start must be on or before end")

    stream = f"backfill:{start.isoformat()}:{end.isoformat()}"

    with db.connect(cfg.database_url) as conn:
        db.migrate(conn)

        resume_from = db.get_watermark(conn, stream)
        cursor = (resume_from + dt.timedelta(days=1)) if resume_from else start
        if cursor > end:
            LOG.info("backfill %s already complete", stream)
            return {"run_id": None, "status": "already_complete"}

        run_id = db.start_run(conn, "backfill", start, end)
        LOG.info("run %d: backfill %s to %s (resuming at %s)", run_id, start, end, cursor)

        totals = {}
        try:
            while cursor <= end:
                chunk_end = min(cursor + dt.timedelta(days=chunk_days - 1), end)
                counters = ingest.load_window(conn, cfg, run_id, cursor, chunk_end)
                for key, value in counters.items():
                    totals[key] = totals.get(key, 0) + value
                db.record_metrics(conn, run_id, totals)
                db.set_watermark(conn, stream, chunk_end)
                cursor = chunk_end + dt.timedelta(days=1)

            transform.build_staging(conn, run_id)
            transform.build_marts(conn, run_id)
            outcome = run_checks(conn, run_id)
            db.record_metrics(conn, run_id, {**totals, **outcome["summary"]})
        except Exception as exc:
            db.finish_run(conn, run_id, "failed", str(exc))
            raise

        status = "failed" if outcome["blocking_failures"] else "succeeded"
        db.finish_run(conn, run_id, status)
        if status == "failed":
            raise QualityGateFailed(
                ", ".join(f["check_name"] for f in outcome["blocking_failures"])
            )
        return {"run_id": run_id, **totals, **outcome["summary"]}
