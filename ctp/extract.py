"""Pulling typed values out of the API's nested modules.

Kept in one place so the raw loader and the transform agree on where a field
lives. Every function here returns None rather than raising: a study missing a
module is normal, and the null budget checks are what decide whether the amount
of missingness is acceptable.
"""

import datetime as dt
from typing import Optional


def _dig(payload: dict, *keys):
    node = payload
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
        if node is None:
            return None
    return node


def nct_id(study: dict) -> Optional[str]:
    return _dig(study, "protocolSection", "identificationModule", "nctId")


def parse_partial_date(value) -> Optional[dt.date]:
    """The API emits YYYY, YYYY-MM and YYYY-MM-DD. Missing parts default to 1."""
    if not value or not isinstance(value, str):
        return None
    parts = value.split("-")
    try:
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else 1
        day = int(parts[2]) if len(parts) > 2 else 1
        return dt.date(year, month, day)
    except (ValueError, IndexError):
        return None


def last_update_post_date(study: dict) -> Optional[dt.date]:
    return parse_partial_date(
        _dig(study, "protocolSection", "statusModule",
             "lastUpdatePostDateStruct", "date")
    )


def _int_or_none(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def flatten(study: dict) -> Optional[dict]:
    """One study payload to one staging row. Returns None if it has no id."""
    ident = _dig(study, "protocolSection", "identificationModule") or {}
    status = _dig(study, "protocolSection", "statusModule") or {}
    design = _dig(study, "protocolSection", "designModule") or {}
    sponsor = _dig(study, "protocolSection", "sponsorCollaboratorsModule") or {}
    elig = _dig(study, "protocolSection", "eligibilityModule") or {}

    study_id = ident.get("nctId")
    if not study_id:
        return None

    lupd = last_update_post_date(study)
    if lupd is None:
        return None

    phases = design.get("phases") or []
    enrollment_info = design.get("enrollmentInfo") or {}
    lead = sponsor.get("leadSponsor") or {}

    return {
        "nct_id": study_id,
        "brief_title": ident.get("briefTitle"),
        "official_title": ident.get("officialTitle"),
        "overall_status": status.get("overallStatus"),
        "study_type": design.get("studyType"),
        "phase": "|".join(phases) if phases else None,
        "enrollment": _int_or_none(enrollment_info.get("count")),
        "enrollment_type": enrollment_info.get("type"),
        "lead_sponsor": lead.get("name"),
        "sponsor_class": lead.get("class"),
        "start_date": parse_partial_date((status.get("startDateStruct") or {}).get("date")),
        "completion_date": parse_partial_date(
            (status.get("completionDateStruct") or {}).get("date")
        ),
        "last_update_post_date": lupd,
        "eligibility_criteria": elig.get("eligibilityCriteria"),
        "healthy_volunteers": elig.get("healthyVolunteers"),
        "sex": elig.get("sex"),
        "minimum_age_text": elig.get("minimumAge"),
        "maximum_age_text": elig.get("maximumAge"),
        "has_results": bool(study.get("hasResults")),
    }


def conditions(study: dict) -> list:
    items = _dig(study, "protocolSection", "conditionsModule", "conditions") or []
    seen, out = set(), []
    for item in items:
        if isinstance(item, str) and item.strip() and item not in seen:
            seen.add(item)
            out.append(item.strip())
    return out


def interventions(study: dict) -> list:
    items = _dig(study, "protocolSection", "armsInterventionsModule", "interventions") or []
    seen, out = set(), []
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = item.get("type") or "UNKNOWN"
        name = (item.get("name") or "").strip()
        if not name or (kind, name) in seen:
            continue
        seen.add((kind, name))
        out.append((kind, name))
    return out
