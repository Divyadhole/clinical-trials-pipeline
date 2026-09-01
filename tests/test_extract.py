import datetime as dt

from ctp import extract


def test_parse_partial_dates():
    assert extract.parse_partial_date("2026-08-29") == dt.date(2026, 8, 29)
    assert extract.parse_partial_date("2026-08") == dt.date(2026, 8, 1)
    assert extract.parse_partial_date("2026") == dt.date(2026, 1, 1)
    assert extract.parse_partial_date("") is None
    assert extract.parse_partial_date(None) is None
    assert extract.parse_partial_date("not a date") is None


def test_flatten_pulls_every_staging_column(sample_study):
    row = extract.flatten(sample_study)
    assert row["nct_id"] == "NCT00000001"
    assert row["overall_status"] == "RECRUITING"
    assert row["phase"] == "PHASE2|PHASE3"
    assert row["enrollment"] == 240
    assert row["lead_sponsor"] == "University of Arizona"
    assert row["start_date"] == dt.date(2025, 3, 1)
    assert row["last_update_post_date"] == dt.date(2026, 8, 27)
    assert row["minimum_age_text"] == "18 Years"
    assert "Inclusion Criteria" in row["eligibility_criteria"]


def test_flatten_returns_none_without_an_id(sample_study):
    del sample_study["protocolSection"]["identificationModule"]["nctId"]
    assert extract.flatten(sample_study) is None


def test_flatten_survives_missing_modules():
    minimal = {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT99999999"},
            "statusModule": {"lastUpdatePostDateStruct": {"date": "2026-08-01"}},
        }
    }
    row = extract.flatten(minimal)
    assert row["nct_id"] == "NCT99999999"
    assert row["enrollment"] is None
    assert row["lead_sponsor"] is None


def test_conditions_and_interventions_are_deduplicated(sample_study):
    sample_study["protocolSection"]["conditionsModule"]["conditions"].append("Obesity")
    assert extract.conditions(sample_study) == ["Type 2 Diabetes", "Obesity"]
    assert extract.interventions(sample_study) == [
        ("DRUG", "Metformin"),
        ("BEHAVIORAL", "Diet counseling"),
    ]
