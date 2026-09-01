"""Command line entry point.

    python -m ctp verify-api
    python -m ctp migrate
    python -m ctp daily
    python -m ctp backfill --start 2026-01-01 --end 2026-03-31
    python -m ctp checks
    python -m ctp status
"""

import argparse
import datetime as dt
import json
import logging
import sys

from . import db, jobs, status
from .config import Config


def _date(value: str) -> dt.date:
    return dt.datetime.strptime(value, "%Y-%m-%d").date()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_verify_api(args) -> int:
    """Hit the live API once and print what actually came back.

    This exists because the v2 docs are thin on the advanced filter syntax, and
    a wrong filter returns 200 with an empty result set rather than an error.
    Run it before trusting a load of zero rows.
    """
    from .api import ClinicalTrialsClient

    end = dt.date.today()
    start = end - dt.timedelta(days=2)
    client = ClinicalTrialsClient(page_size=5)

    params = {
        "filter.advanced": (
            f"AREA[LastUpdatePostDate]RANGE[{start.isoformat()},{end.isoformat()}]"
        ),
        "pageSize": 5,
        "countTotal": "true",
        "sort": "LastUpdatePostDate",
    }
    payload = client._get(params)

    print("envelope keys      :", sorted(payload.keys()))
    print("totalCount         :", payload.get("totalCount"))
    print("nextPageToken      :", (payload.get("nextPageToken") or "")[:24] or "(none)")
    print("studies returned   :", len(payload.get("studies") or []))

    studies = payload.get("studies") or []
    if not studies:
        print("\nZERO STUDIES for a two day window. Either the filter syntax is "
              "wrong or the source is down. Do not build on this.")
        return 1

    from . import extract
    first = studies[0]
    print("first nct_id       :", extract.nct_id(first))
    print("first last update  :", extract.last_update_post_date(first))
    print("top level keys     :", sorted(first.keys()))
    print("protocolSection    :", sorted((first.get("protocolSection") or {}).keys()))

    in_window = [extract.last_update_post_date(s) for s in studies]
    outside = [d for d in in_window if d and not (start <= d <= end)]
    if outside:
        print(f"\nFILTER NOT RESPECTED: {len(outside)} of {len(studies)} outside "
              f"the requested window, e.g. {outside[0]}")
        return 1

    print(f"\nFilter respected: all {len(studies)} sampled studies fall in "
          f"[{start}, {end}].")
    return 0


def cmd_migrate(args) -> int:
    cfg = Config.from_env()
    with db.connect(cfg.database_url) as conn:
        applied = db.migrate(conn)
    print(f"applied {len(applied)} migration(s): {applied or 'none, already current'}")
    return 0


def cmd_daily(args) -> int:
    result = jobs.run_daily()
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_backfill(args) -> int:
    result = jobs.run_backfill(start=args.start, end=args.end, chunk_days=args.chunk_days)
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_checks(args) -> int:
    from .quality import run_checks

    cfg = Config.from_env()
    with db.connect(cfg.database_url) as conn:
        run_id = db.start_run(conn, "checks")
        outcome = run_checks(conn, run_id)
        blocking = outcome["blocking_failures"]
        db.finish_run(conn, run_id, "failed" if blocking else "succeeded")
    print(json.dumps(outcome["summary"], indent=2))
    return 1 if blocking else 0


def cmd_status(args) -> int:
    cfg = Config.from_env()
    with db.connect(cfg.database_url) as conn:
        path = status.write(conn)
    print(f"wrote {path}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ctp", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("verify-api", help="check the live API contract").set_defaults(fn=cmd_verify_api)
    sub.add_parser("migrate", help="apply SQL migrations").set_defaults(fn=cmd_migrate)
    sub.add_parser("daily", help="incremental load from the watermark").set_defaults(fn=cmd_daily)
    sub.add_parser("checks", help="run the quality suite only").set_defaults(fn=cmd_checks)
    sub.add_parser("status", help="regenerate docs/index.html").set_defaults(fn=cmd_status)

    bf = sub.add_parser("backfill", help="load a historical date range")
    bf.add_argument("--start", type=_date, required=True)
    bf.add_argument("--end", type=_date, required=True)
    bf.add_argument("--chunk-days", type=int, default=7)
    bf.set_defaults(fn=cmd_backfill)

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
