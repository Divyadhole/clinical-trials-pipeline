"""Raw load. Fetch a date window, classify each study, upsert, record history.

The load is idempotent by construction. Running the same window twice inserts
zero new rows into raw.studies_history and leaves raw.studies row count
unchanged, because a study is only written to history when its content hash is
one we have not seen for that NCT id.
"""

import json
import logging

import psycopg2.extras

from . import db, extract
from .api import ClinicalTrialsClient, content_hash, field_paths

LOG = logging.getLogger(__name__)

BATCH_SIZE = 500
FINGERPRINT_SAMPLE = 200

UPSERT_SQL = """
insert into raw.studies (
    nct_id, last_update_post_date, content_hash, payload,
    first_seen_run_id, last_seen_run_id
)
values %s
on conflict (nct_id) do update set
    last_seen_at          = now(),
    last_seen_run_id      = excluded.last_seen_run_id,
    last_update_post_date = excluded.last_update_post_date,
    content_hash          = excluded.content_hash,
    payload               = excluded.payload,
    last_changed_at       = case
        when raw.studies.content_hash is distinct from excluded.content_hash
        then now()
        else raw.studies.last_changed_at
    end
"""

HISTORY_SQL = """
insert into raw.studies_history (
    nct_id, content_hash, last_update_post_date, payload, run_id
)
values %s
on conflict (nct_id, content_hash) do nothing
"""


def _flush(conn, run_id, batch, counters):
    """Write one batch and update the new/changed/unchanged counters."""
    if not batch:
        return

    ids = [row["nct_id"] for row in batch]
    with conn.cursor() as cur:
        cur.execute(
            "select nct_id, content_hash from raw.studies where nct_id = any(%s)",
            (ids,),
        )
        existing = dict(cur.fetchall())

    to_history = []
    for row in batch:
        prior = existing.get(row["nct_id"])
        if prior is None:
            counters["studies_new"] += 1
            to_history.append(row)
        elif prior != row["content_hash"]:
            counters["studies_changed"] += 1
            to_history.append(row)
        else:
            counters["studies_unchanged"] += 1

    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            UPSERT_SQL,
            [
                (
                    r["nct_id"],
                    r["last_update_post_date"],
                    r["content_hash"],
                    json.dumps(r["payload"]),
                    run_id,
                    run_id,
                )
                for r in batch
            ],
            page_size=BATCH_SIZE,
        )
        if to_history:
            psycopg2.extras.execute_values(
                cur,
                HISTORY_SQL,
                [
                    (
                        r["nct_id"],
                        r["content_hash"],
                        r["last_update_post_date"],
                        json.dumps(r["payload"]),
                        run_id,
                    )
                    for r in to_history
                ],
                page_size=BATCH_SIZE,
            )
    conn.commit()
    batch.clear()


def _record_fingerprint(conn, run_id, sample_paths):
    if not sample_paths:
        return
    import hashlib

    ordered = sorted(sample_paths)
    fingerprint = hashlib.sha256("\n".join(ordered).encode("utf-8")).hexdigest()
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into ops.schema_fingerprints (run_id, fingerprint, field_paths)
            values (%s, %s, %s)
            """,
            (run_id, fingerprint, json.dumps(ordered)),
        )
    conn.commit()


def load_window(conn, cfg, run_id: int, window_start, window_end) -> dict:
    """Load every study updated in [window_start, window_end]. Returns metrics."""
    client = ClinicalTrialsClient(
        requests_per_minute=cfg.requests_per_minute,
        max_retries=cfg.max_retries,
        page_size=cfg.page_size,
    )

    counters = {
        "studies_fetched": 0,
        "studies_new": 0,
        "studies_changed": 0,
        "studies_unchanged": 0,
        "studies_skipped_no_id": 0,
    }
    batch = []
    sample_paths = set()
    sampled = 0

    LOG.info("loading window %s to %s", window_start, window_end)
    for study in client.iter_studies(window_start, window_end):
        counters["studies_fetched"] += 1

        study_id = extract.nct_id(study)
        lupd = extract.last_update_post_date(study)
        if not study_id or lupd is None:
            counters["studies_skipped_no_id"] += 1
            continue

        if sampled < FINGERPRINT_SAMPLE:
            sample_paths |= field_paths(study)
            sampled += 1

        batch.append(
            {
                "nct_id": study_id,
                "last_update_post_date": lupd,
                "content_hash": content_hash(study),
                "payload": study,
            }
        )
        if len(batch) >= BATCH_SIZE:
            _flush(conn, run_id, batch, counters)

    _flush(conn, run_id, batch, counters)
    _record_fingerprint(conn, run_id, sample_paths)

    counters["api_requests"] = client.request_count
    counters["api_retries"] = client.retry_count
    db.record_metrics(conn, run_id, counters)
    LOG.info("window loaded: %s", counters)
    return counters
