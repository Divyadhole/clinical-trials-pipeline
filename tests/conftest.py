import copy
import datetime as dt
import os

import pytest


def _study(nct_id="NCT00000001", last_update="2026-08-27", title="A Study of Something"):
    return {
        "hasResults": False,
        "protocolSection": {
            "identificationModule": {
                "nctId": nct_id,
                "briefTitle": title,
                "officialTitle": f"{title}: A Randomized Trial",
            },
            "statusModule": {
                "overallStatus": "RECRUITING",
                "startDateStruct": {"date": "2025-03"},
                "completionDateStruct": {"date": "2027-12-31"},
                "lastUpdatePostDateStruct": {"date": last_update},
            },
            "designModule": {
                "studyType": "INTERVENTIONAL",
                "phases": ["PHASE2", "PHASE3"],
                "enrollmentInfo": {"count": 240, "type": "ESTIMATED"},
            },
            "sponsorCollaboratorsModule": {
                "leadSponsor": {"name": "University of Arizona", "class": "OTHER"}
            },
            "conditionsModule": {"conditions": ["Type 2 Diabetes", "Obesity"]},
            "armsInterventionsModule": {
                "interventions": [
                    {"type": "DRUG", "name": "Metformin"},
                    {"type": "BEHAVIORAL", "name": "Diet counseling"},
                ]
            },
            "eligibilityModule": {
                "eligibilityCriteria": "Inclusion Criteria:\n* Age 18 to 65\n"
                                       "Exclusion Criteria:\n* Pregnancy",
                "healthyVolunteers": False,
                "sex": "ALL",
                "minimumAge": "18 Years",
                "maximumAge": "65 Years",
            },
        },
    }


@pytest.fixture
def sample_study():
    return _study()


@pytest.fixture
def make_study():
    def _make(**kwargs):
        return copy.deepcopy(_study(**kwargs))
    return _make


@pytest.fixture
def today():
    return dt.date(2026, 8, 29)


@pytest.fixture
def db_conn():
    """Integration fixture. Skips cleanly when no database is configured."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set; skipping integration test")

    import psycopg2

    from ctp import db as db_module

    conn = psycopg2.connect(url)
    db_module.migrate(conn)
    with conn.cursor() as cur:
        # Each integration test starts from a clean slate. Safe: these schemas
        # are created by this project and by nothing else.
        cur.execute(
            "truncate marts.bridge_trial_condition, marts.fct_trials, "
            "marts.dim_sponsor, staging.study_conditions, "
            "staging.study_interventions, staging.studies, "
            "raw.studies_history, raw.studies, ops.test_results, "
            "ops.run_metrics, ops.schema_fingerprints, ops.pipeline_runs, "
            "ops.watermarks restart identity cascade"
        )
    conn.commit()
    yield conn
    conn.close()
