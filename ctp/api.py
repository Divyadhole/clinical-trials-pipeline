"""ClinicalTrials.gov API v2 client.

Three things this has to get right, and they are the whole reason the source
was chosen:

1. Pagination is cursor based. The response carries `nextPageToken`; you send
   it back as `pageToken`. There is no offset, so you cannot skip ahead and you
   cannot resume a page you did not finish.
2. The public limit is roughly 50 requests per minute per IP, enforced with
   429s rather than documented. We stay under it on purpose and back off when
   we are wrong.
3. Studies are revised after registration, so `LastUpdatePostDate` is the only
   sane incremental key, and the same NCT id will legitimately arrive many
   times over the life of the pipeline.
"""

import hashlib
import json
import logging
import random
import threading
import time
from typing import Iterator

import requests

LOG = logging.getLogger(__name__)

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
USER_AGENT = "clinical-trials-pipeline/0.1 (+https://github.com/Divyadhole/clinical-trials-pipeline)"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class RateLimiter:
    """Simple thread-safe minimum-interval limiter."""

    def __init__(self, requests_per_minute: int):
        self._interval = 60.0 / max(requests_per_minute, 1)
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            sleep_for = self._next_allowed - now
            if sleep_for > 0:
                time.sleep(sleep_for)
                now = time.monotonic()
            self._next_allowed = now + self._interval


class ApiError(RuntimeError):
    pass


class ClinicalTrialsClient:
    def __init__(self, requests_per_minute: int = 40, max_retries: int = 5,
                 page_size: int = 1000, timeout: int = 60):
        self.limiter = RateLimiter(requests_per_minute)
        self.max_retries = max_retries
        self.page_size = min(page_size, 1000)  # API hard cap
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        self.request_count = 0
        self.retry_count = 0

    def _get(self, params: dict) -> dict:
        last_error = None
        for attempt in range(self.max_retries + 1):
            self.limiter.wait()
            try:
                self.request_count += 1
                resp = self.session.get(BASE_URL, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                resp = None

            if resp is not None and resp.status_code == 200:
                return resp.json()

            if resp is not None and resp.status_code not in RETRYABLE_STATUS:
                raise ApiError(
                    f"HTTP {resp.status_code} from API: {resp.text[:300]}"
                )

            if attempt == self.max_retries:
                break

            self.retry_count += 1
            # Honour Retry-After when the server sends one, otherwise exponential
            # backoff with jitter so a retry storm does not sync up.
            delay = None
            if resp is not None:
                header = resp.headers.get("Retry-After")
                if header and header.isdigit():
                    delay = float(header)
            if delay is None:
                delay = min(60.0, (2 ** attempt)) + random.uniform(0, 1)
            status = resp.status_code if resp is not None else "connection error"
            LOG.warning("retrying after %s, sleeping %.1fs (attempt %d/%d)",
                        status, delay, attempt + 1, self.max_retries)
            time.sleep(delay)

        raise ApiError(f"gave up after {self.max_retries} retries: {last_error}")

    def iter_studies(self, window_start, window_end, count_total: bool = True) -> Iterator[dict]:
        """Yield every study whose LastUpdatePostDate falls in [start, end].

        Both bounds are inclusive, which matters: it means consecutive daily
        runs overlap by a day. That overlap is deliberate. A record posted late
        in the day would otherwise be missed, and the upsert makes the overlap
        free.
        """
        params = {
            "filter.advanced": (
                f"AREA[LastUpdatePostDate]RANGE"
                f"[{window_start.isoformat()},{window_end.isoformat()}]"
            ),
            "pageSize": self.page_size,
            "sort": "LastUpdatePostDate",
        }
        if count_total:
            params["countTotal"] = "true"

        page = 0
        while True:
            payload = self._get(params)
            studies = payload.get("studies") or []
            page += 1
            LOG.info("page %d: %d studies (total reported: %s)",
                     page, len(studies), payload.get("totalCount", "n/a"))
            for study in studies:
                yield study

            token = payload.get("nextPageToken")
            if not token:
                return
            params = dict(params)
            params["pageToken"] = token
            params.pop("countTotal", None)


def content_hash(study: dict) -> str:
    """Stable hash of a study payload.

    Sorted keys and separators matter. If this is not deterministic, every run
    looks like a change, the history table grows without bound, and the
    idempotency test starts failing for no real reason.
    """
    blob = json.dumps(study, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def field_paths(study: dict, prefix: str = "", depth: int = 0, max_depth: int = 3) -> set:
    """Set of JSON paths in a payload, used for schema drift detection."""
    paths = set()
    if depth >= max_depth or not isinstance(study, dict):
        return paths
    for key, value in study.items():
        path = f"{prefix}.{key}" if prefix else key
        paths.add(path)
        if isinstance(value, dict):
            paths |= field_paths(value, path, depth + 1, max_depth)
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            paths |= field_paths(value[0], f"{path}[]", depth + 1, max_depth)
    return paths
