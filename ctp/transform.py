"""raw -> staging -> marts.

Both steps are incremental: they process only the studies this run touched,
identified by last_seen_run_id. Both are also re-runnable, because every write
is an upsert keyed on the natural key and the child tables are deleted and
rewritten per study rather than appended to.
"""

import logging

import psycopg2.extras

from . import db, extract

LOG = logging.getLogger(__name__)

BATCH = 500

STAGING_COLUMNS = [
    "nct_id", "brief_title", "official_title", "overall_status", "study_type",
    "phase", "enrollment", "enrollment_type", "lead_sponsor", "sponsor_class",
    "start_date", "completion_date", "last_update_post_date",
    "eligibility_criteria", "healthy_volunteers", "sex", "minimum_age_text",
    "maximum_age_text", "has_results",
]

STAGING_UPSERT = f"""
insert into staging.studies ({", ".join(STAGING_COLUMNS)}, loaded_at)
values %s
on conflict (nct_id) do update set
    {", ".join(f"{c} = excluded.{c}" for c in STAGING_COLUMNS if c != "nct_id")},
    loaded_at = now()
"""


def _touched_ids(conn, run_id):
    with conn.cursor() as cur:
        cur.execute(
            "select nct_id from raw.studies where last_seen_run_id = %s order by nct_id",
            (run_id,),
        )
        return [row[0] for row in cur.fetchall()]


def build_staging(conn, run_id: int) -> dict:
    ids = _touched_ids(conn, run_id)
    metrics = {"staging_rows_upserted": 0, "staging_conditions": 0,
               "staging_interventions": 0, "staging_unparsable": 0}

    for start in range(0, len(ids), BATCH):
        chunk = ids[start:start + BATCH]
        with conn.cursor() as cur:
            cur.execute(
                "select nct_id, payload from raw.studies where nct_id = any(%s)",
                (chunk,),
            )
            payloads = cur.fetchall()

        rows, conditions, interventions = [], [], []
        for study_id, payload in payloads:
            flat = extract.flatten(payload)
            if flat is None:
                metrics["staging_unparsable"] += 1
                continue
            rows.append(tuple(flat[c] for c in STAGING_COLUMNS))
            conditions.extend((study_id, c) for c in extract.conditions(payload))
            interventions.extend(
                (study_id, kind, name) for kind, name in extract.interventions(payload)
            )

        if not rows:
            continue

        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur, STAGING_UPSERT, rows,
                template="(" + ", ".join(["%s"] * len(STAGING_COLUMNS)) + ", now())",
                page_size=BATCH,
            )
            # Child tables are rewritten wholesale per study. A condition removed
            # upstream has to disappear here too, and an upsert alone would leave
            # the stale row behind.
            cur.execute("delete from staging.study_conditions where nct_id = any(%s)", (chunk,))
            cur.execute("delete from staging.study_interventions where nct_id = any(%s)", (chunk,))
            if conditions:
                psycopg2.extras.execute_values(
                    cur,
                    "insert into staging.study_conditions (nct_id, condition) values %s "
                    "on conflict do nothing",
                    conditions, page_size=BATCH,
                )
            if interventions:
                psycopg2.extras.execute_values(
                    cur,
                    "insert into staging.study_interventions "
                    "(nct_id, intervention_type, intervention_name) values %s "
                    "on conflict do nothing",
                    interventions, page_size=BATCH,
                )
        conn.commit()

        metrics["staging_rows_upserted"] += len(rows)
        metrics["staging_conditions"] += len(conditions)
        metrics["staging_interventions"] += len(interventions)

    db.record_metrics(conn, run_id, metrics)
    LOG.info("staging built: %s", metrics)
    return metrics


def build_marts(conn, run_id: int) -> dict:
    ids = _touched_ids(conn, run_id)
    metrics = {"marts_rows_upserted": 0}

    for start in range(0, len(ids), BATCH):
        chunk = ids[start:start + BATCH]
        with conn.cursor() as cur:
            # Sponsors first: fct_trials carries a foreign key to them.
            cur.execute(
                """
                insert into marts.dim_sponsor (lead_sponsor, sponsor_class)
                select distinct s.lead_sponsor, s.sponsor_class
                  from staging.studies s
                 where s.nct_id = any(%s) and s.lead_sponsor is not null
                on conflict (lead_sponsor) do update
                    set sponsor_class = coalesce(excluded.sponsor_class,
                                                 marts.dim_sponsor.sponsor_class)
                """,
                (chunk,),
            )
            cur.execute(
                """
                insert into marts.fct_trials (
                    nct_id, sponsor_key, overall_status, study_type, phase,
                    enrollment, start_date, completion_date,
                    last_update_post_date, has_results,
                    condition_count, intervention_count, eligibility_char_len
                )
                select
                    s.nct_id,
                    d.sponsor_key,
                    s.overall_status,
                    s.study_type,
                    s.phase,
                    s.enrollment,
                    s.start_date,
                    s.completion_date,
                    s.last_update_post_date,
                    s.has_results,
                    (select count(*) from staging.study_conditions c
                      where c.nct_id = s.nct_id),
                    (select count(*) from staging.study_interventions i
                      where i.nct_id = s.nct_id),
                    length(coalesce(s.eligibility_criteria, ''))
                  from staging.studies s
                  left join marts.dim_sponsor d on d.lead_sponsor = s.lead_sponsor
                 where s.nct_id = any(%s)
                on conflict (nct_id) do update set
                    sponsor_key           = excluded.sponsor_key,
                    overall_status        = excluded.overall_status,
                    study_type            = excluded.study_type,
                    phase                 = excluded.phase,
                    enrollment            = excluded.enrollment,
                    start_date            = excluded.start_date,
                    completion_date       = excluded.completion_date,
                    last_update_post_date = excluded.last_update_post_date,
                    has_results           = excluded.has_results,
                    condition_count       = excluded.condition_count,
                    intervention_count    = excluded.intervention_count,
                    eligibility_char_len  = excluded.eligibility_char_len,
                    built_at              = now()
                """,
                (chunk,),
            )
            metrics["marts_rows_upserted"] += cur.rowcount

            cur.execute(
                "delete from marts.bridge_trial_condition where nct_id = any(%s)", (chunk,)
            )
            cur.execute(
                """
                insert into marts.bridge_trial_condition (nct_id, condition)
                select c.nct_id, c.condition
                  from staging.study_conditions c
                  join marts.fct_trials f on f.nct_id = c.nct_id
                 where c.nct_id = any(%s)
                on conflict do nothing
                """,
                (chunk,),
            )
        conn.commit()

    db.record_metrics(conn, run_id, metrics)
    LOG.info("marts built: %s", metrics)
    return metrics
