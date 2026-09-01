"""The data quality suite.

Every check here is written so that it can actually fail. A check that can only
ever pass is decoration. Severity 'error' fails the run and stops the watermark
from advancing; severity 'warn' is recorded and visible on the status page but
does not stop the pipeline.

Thresholds were chosen by looking at real loads, not by picking round numbers.
Where a threshold is a guess, the comment says so.
"""

from dataclasses import dataclass

KNOWN_STATUSES = (
    "RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION",
    "ACTIVE_NOT_RECRUITING", "SUSPENDED", "TERMINATED", "COMPLETED",
    "WITHDRAWN", "UNKNOWN", "AVAILABLE", "NO_LONGER_AVAILABLE",
    "TEMPORARILY_NOT_AVAILABLE", "APPROVED_FOR_MARKETING", "WITHHELD",
)


@dataclass(frozen=True)
class SqlCheck:
    name: str
    target: str
    severity: str
    sql: str
    threshold: float
    compare: str  # "lte" | "gte" | "eq"
    detail: str

    def passed(self, observed) -> bool:
        if observed is None:
            return False
        if self.compare == "lte":
            return observed <= self.threshold
        if self.compare == "gte":
            return observed >= self.threshold
        return observed == self.threshold


SQL_CHECKS = [
    # --- freshness -------------------------------------------------------
    SqlCheck(
        name="freshness_raw",
        target="raw.studies",
        severity="error",
        sql="""select coalesce(
                   extract(day from now() - max(last_update_post_date)::timestamptz),
                   9999)
                 from raw.studies""",
        threshold=4,
        compare="lte",
        detail="Days since the newest LastUpdatePostDate in raw. The source posts "
               "daily; more than four days stale means we are not actually running.",
    ),
    # --- completeness across layers --------------------------------------
    SqlCheck(
        name="raw_staging_parity",
        target="staging.studies",
        severity="error",
        sql="""select (select count(*) from raw.studies)
                    - (select count(*) from staging.studies)""",
        threshold=0,
        compare="eq",
        detail="Every raw study must have exactly one staging row. A nonzero gap "
               "means the transform silently dropped records.",
    ),
    SqlCheck(
        name="history_covers_current",
        target="raw.studies_history",
        severity="error",
        sql="""select count(*) from raw.studies s
                where not exists (
                    select 1 from raw.studies_history h
                     where h.nct_id = s.nct_id and h.content_hash = s.content_hash)""",
        threshold=0,
        compare="eq",
        detail="The current version of every study must exist in history. If not, "
               "the change-tracking write path is broken.",
    ),
    # --- null budgets ----------------------------------------------------
    SqlCheck(
        name="null_budget_brief_title",
        target="staging.studies.brief_title",
        severity="error",
        sql="""select coalesce(
                   count(*) filter (where brief_title is null)::numeric
                   / nullif(count(*), 0), 0)
                 from staging.studies""",
        threshold=0.01,
        compare="lte",
        detail="Brief title is required by the registry. Above one percent null "
               "means we are parsing the wrong field, not that data is missing.",
    ),
    SqlCheck(
        name="null_budget_overall_status",
        target="staging.studies.overall_status",
        severity="error",
        sql="""select coalesce(
                   count(*) filter (where overall_status is null)::numeric
                   / nullif(count(*), 0), 0)
                 from staging.studies""",
        threshold=0.001,
        compare="lte",
        detail="Status is always populated upstream.",
    ),
    SqlCheck(
        name="null_budget_eligibility_criteria",
        target="staging.studies.eligibility_criteria",
        severity="warn",
        sql="""select coalesce(
                   count(*) filter (where eligibility_criteria is null
                                       or length(eligibility_criteria) = 0)::numeric
                   / nullif(count(*), 0), 0)
                 from staging.studies""",
        threshold=0.20,
        compare="lte",
        detail="Free-text eligibility is genuinely absent for some study types, so "
               "this is a warning. Twenty percent is calibrated from observed loads "
               "and should be tightened once there is a month of history.",
    ),
    SqlCheck(
        name="null_budget_lead_sponsor",
        target="staging.studies.lead_sponsor",
        severity="warn",
        sql="""select coalesce(
                   count(*) filter (where lead_sponsor is null)::numeric
                   / nullif(count(*), 0), 0)
                 from staging.studies""",
        threshold=0.02,
        compare="lte",
        detail="Sponsor drives the dim_sponsor join; nulls become unattributed trials.",
    ),
    # --- uniqueness ------------------------------------------------------
    SqlCheck(
        name="unique_nct_id_staging",
        target="staging.studies.nct_id",
        severity="error",
        sql="""select count(*) from (
                   select nct_id from staging.studies
                   group by nct_id having count(*) > 1) d""",
        threshold=0,
        compare="eq",
        detail="Grain check. Enforced by the primary key, asserted anyway so a "
               "future schema change cannot quietly remove it.",
    ),
    SqlCheck(
        name="unique_nct_id_marts",
        target="marts.fct_trials.nct_id",
        severity="error",
        sql="""select count(*) from (
                   select nct_id from marts.fct_trials
                   group by nct_id having count(*) > 1) d""",
        threshold=0,
        compare="eq",
        detail="One row per trial in the fact table.",
    ),
    # --- referential integrity -------------------------------------------
    SqlCheck(
        name="ri_conditions_to_studies",
        target="staging.study_conditions",
        severity="error",
        sql="""select count(*) from staging.study_conditions c
                where not exists (select 1 from staging.studies s
                                   where s.nct_id = c.nct_id)""",
        threshold=0,
        compare="eq",
        detail="Orphaned condition rows.",
    ),
    SqlCheck(
        name="ri_bridge_to_fct",
        target="marts.bridge_trial_condition",
        severity="error",
        sql="""select count(*) from marts.bridge_trial_condition b
                where not exists (select 1 from marts.fct_trials f
                                   where f.nct_id = b.nct_id)""",
        threshold=0,
        compare="eq",
        detail="Bridge rows pointing at trials that do not exist.",
    ),
    SqlCheck(
        name="ri_fct_to_dim_sponsor",
        target="marts.fct_trials.sponsor_key",
        severity="error",
        sql="""select count(*) from marts.fct_trials f
                where f.sponsor_key is not null
                  and not exists (select 1 from marts.dim_sponsor d
                                   where d.sponsor_key = f.sponsor_key)""",
        threshold=0,
        compare="eq",
        detail="Broken sponsor foreign key.",
    ),
    SqlCheck(
        name="fct_staging_parity",
        target="marts.fct_trials",
        severity="error",
        sql="""select (select count(*) from staging.studies)
                    - (select count(*) from marts.fct_trials)""",
        threshold=0,
        compare="eq",
        detail="Every staging study should reach the fact table.",
    ),
    # --- accepted values and sanity --------------------------------------
    SqlCheck(
        name="accepted_values_overall_status",
        target="staging.studies.overall_status",
        severity="warn",
        sql="""select count(*) from staging.studies
                where overall_status is not null
                  and overall_status not in %s""" % (KNOWN_STATUSES,),
        threshold=0,
        compare="eq",
        detail="A status we have never seen. Warning rather than error because the "
               "registry does add values, but it needs a human to look.",
    ),
    SqlCheck(
        name="enrollment_not_negative",
        target="staging.studies.enrollment",
        severity="error",
        sql="select count(*) from staging.studies where enrollment < 0",
        threshold=0,
        compare="eq",
        detail="Negative enrollment means a parsing error, not a real value.",
    ),
    SqlCheck(
        name="completion_after_start",
        target="staging.studies",
        severity="warn",
        sql="""select count(*) from staging.studies
                where start_date is not null and completion_date is not null
                  and completion_date < start_date""",
        threshold=0,
        compare="eq",
        detail="Registry data does contain a handful of these, so it is a warning. "
               "A sudden jump is the signal, not the existence of any at all.",
    ),
]


def check_schema_drift(conn, run_id):
    """Compare this run's field paths to the previous run's.

    This is the check most likely to catch a real upstream change, and the one
    most likely to be missing from a portfolio project.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            select field_paths from ops.schema_fingerprints
             order by id desc limit 2
            """
        )
        rows = cur.fetchall()

    if len(rows) < 2:
        return {
            "check_name": "schema_drift",
            "target": "raw.studies.payload",
            "severity": "warn",
            "status": "pass",
            "observed": 0,
            "threshold": 0,
            "detail": "No previous fingerprint to compare against yet.",
        }

    current, previous = set(rows[0][0]), set(rows[1][0])
    added = sorted(current - previous)
    removed = sorted(previous - current)
    changed = len(added) + len(removed)

    parts = []
    if added:
        parts.append("new: " + ", ".join(added[:8]))
    if removed:
        parts.append("gone: " + ", ".join(removed[:8]))

    return {
        "check_name": "schema_drift",
        "target": "raw.studies.payload",
        "severity": "warn",
        "status": "pass" if changed == 0 else "fail",
        "observed": changed,
        "threshold": 0,
        "detail": "; ".join(parts) or "No change in observed field paths.",
    }


def check_volume_anomaly(conn, run_id):
    """This run's fetch count against the median of the last seven ingest runs.

    Catches the failure where the API returns 200 with an empty result set and
    the pipeline reports a cheerful success having loaded nothing.
    """
    with conn.cursor() as cur:
        cur.execute(
            "select value from ops.run_metrics where run_id = %s and metric = 'studies_fetched'",
            (run_id,),
        )
        row = cur.fetchone()
        current = float(row[0]) if row else 0.0

        cur.execute(
            """
            select percentile_cont(0.5) within group (order by m.value)
              from ops.run_metrics m
              join ops.pipeline_runs r on r.run_id = m.run_id
             where m.metric = 'studies_fetched'
               and m.run_id <> %s
               and r.job = 'daily'
               and r.status = 'succeeded'
               and m.run_id in (
                    select run_id from ops.pipeline_runs
                     where job = 'daily' and status = 'succeeded'
                     order by run_id desc limit 7)
            """,
            (run_id,),
        )
        median_row = cur.fetchone()
        median = float(median_row[0]) if median_row and median_row[0] is not None else None

    if median is None or median == 0:
        return {
            "check_name": "volume_anomaly",
            "target": "ops.run_metrics.studies_fetched",
            "severity": "warn",
            "status": "pass",
            "observed": current,
            "threshold": 0,
            "detail": f"Not enough run history to judge volume yet (fetched {current:.0f}).",
        }

    ratio = current / median
    ok = 0.2 <= ratio <= 5.0
    return {
        "check_name": "volume_anomaly",
        "target": "ops.run_metrics.studies_fetched",
        "severity": "warn",
        "status": "pass" if ok else "fail",
        "observed": round(ratio, 3),
        "threshold": 5.0,
        "detail": f"Fetched {current:.0f} against a trailing median of {median:.0f}.",
    }


PYTHON_CHECKS = [check_schema_drift, check_volume_anomaly]
