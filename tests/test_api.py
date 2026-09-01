import datetime as dt
import time

from ctp.api import ClinicalTrialsClient, RateLimiter, content_hash, field_paths


class FakeClient(ClinicalTrialsClient):
    """Replaces the network with three canned pages."""

    def __init__(self, pages):
        super().__init__(requests_per_minute=6000, page_size=2)
        self.pages = pages
        self.calls = []

    def _get(self, params):
        self.calls.append(dict(params))
        return self.pages[len(self.calls) - 1]


def test_pagination_follows_next_page_token(make_study):
    pages = [
        {"studies": [make_study(nct_id="NCT1"), make_study(nct_id="NCT2")],
         "nextPageToken": "tok-a", "totalCount": 5},
        {"studies": [make_study(nct_id="NCT3"), make_study(nct_id="NCT4")],
         "nextPageToken": "tok-b"},
        {"studies": [make_study(nct_id="NCT5")]},
    ]
    client = FakeClient(pages)
    got = list(client.iter_studies(dt.date(2026, 8, 1), dt.date(2026, 8, 2)))

    assert len(got) == 5
    assert len(client.calls) == 3
    # The token from each page is sent as pageToken on the next request.
    assert "pageToken" not in client.calls[0]
    assert client.calls[1]["pageToken"] == "tok-a"
    assert client.calls[2]["pageToken"] == "tok-b"
    # countTotal is asked for once, not on every page.
    assert client.calls[0].get("countTotal") == "true"
    assert "countTotal" not in client.calls[2]


def test_filter_expresses_the_requested_window():
    client = FakeClient([{"studies": []}])
    list(client.iter_studies(dt.date(2026, 1, 5), dt.date(2026, 1, 9)))
    assert client.calls[0]["filter.advanced"] == (
        "AREA[LastUpdatePostDate]RANGE[2026-01-05,2026-01-09]"
    )


def test_page_size_is_capped_at_the_api_maximum():
    assert ClinicalTrialsClient(page_size=50000).page_size == 1000


def test_content_hash_is_order_independent():
    a = {"x": 1, "y": {"b": 2, "a": 3}}
    b = {"y": {"a": 3, "b": 2}, "x": 1}
    assert content_hash(a) == content_hash(b)


def test_content_hash_changes_when_content_changes(make_study):
    one = make_study(title="Study A")
    two = make_study(title="Study B")
    assert content_hash(one) != content_hash(two)


def test_field_paths_walks_nested_modules(sample_study):
    paths = field_paths(sample_study)
    assert "protocolSection" in paths
    assert "protocolSection.identificationModule" in paths
    assert "protocolSection.conditionsModule" in paths


def test_rate_limiter_enforces_the_interval():
    limiter = RateLimiter(requests_per_minute=600)  # 100ms apart
    start = time.monotonic()
    for _ in range(3):
        limiter.wait()
    assert time.monotonic() - start >= 0.18
