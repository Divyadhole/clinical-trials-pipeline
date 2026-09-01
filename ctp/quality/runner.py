"""Execute the quality suite, record every result, decide whether the run failed."""

import logging

import psycopg2.extras

from .checks import PYTHON_CHECKS, SQL_CHECKS

LOG = logging.getLogger(__name__)

INSERT_SQL = """
insert into ops.test_results
    (run_id, check_name, target, severity, status, observed, threshold, detail)
values %s
"""


def run_checks(conn, run_id: int) -> dict:
    results = []

    for check in SQL_CHECKS:
        try:
            with conn.cursor() as cur:
                cur.execute(check.sql)
                row = cur.fetchone()
            observed = float(row[0]) if row and row[0] is not None else None
            status = "pass" if check.passed(observed) else "fail"
            detail = check.detail
        except Exception as exc:  # a check that errors is a failing check
            conn.rollback()
            observed, status = None, "fail"
            detail = f"check raised: {exc}"

        results.append(
            {
                "check_name": check.name,
                "target": check.target,
                "severity": check.severity,
                "status": status,
                "observed": observed,
                "threshold": check.threshold,
                "detail": detail,
            }
        )

    for fn in PYTHON_CHECKS:
        try:
            results.append(fn(conn, run_id))
        except Exception as exc:
            conn.rollback()
            results.append(
                {
                    "check_name": fn.__name__.replace("check_", ""),
                    "target": "-",
                    "severity": "warn",
                    "status": "fail",
                    "observed": None,
                    "threshold": None,
                    "detail": f"check raised: {exc}",
                }
            )

    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            INSERT_SQL,
            [
                (
                    run_id, r["check_name"], r["target"], r["severity"],
                    r["status"], r["observed"], r["threshold"], r["detail"],
                )
                for r in results
            ],
        )
    conn.commit()

    failures = [r for r in results if r["status"] == "fail"]
    errors = [r for r in failures if r["severity"] == "error"]
    warnings = [r for r in failures if r["severity"] == "warn"]

    for r in failures:
        LOG.warning("%-8s %-32s observed=%s threshold=%s :: %s",
                    r["severity"].upper(), r["check_name"], r["observed"],
                    r["threshold"], r["detail"])

    summary = {
        "checks_run": len(results),
        "checks_passed": len(results) - len(failures),
        "checks_failed_error": len(errors),
        "checks_failed_warn": len(warnings),
    }
    LOG.info("quality suite: %s", summary)
    return {"summary": summary, "results": results, "blocking_failures": errors}
