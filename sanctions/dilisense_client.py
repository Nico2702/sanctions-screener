"""
Dilisense API client for the NaroIX Sanctions Screener.

Wraps the /checkEntity endpoint with clear error handling,
a retry policy for transient failures (timeouts + 5xx errors),
and a compact response structure used throughout the app.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

import time

import requests


DILISENSE_BASE_URL = "https://api.dilisense.com/v1"

# Timeout per HTTP request. Dilisense can take 3-5s on simple calls
# and occasionally longer on batched queries against 130+ lists.
REQUEST_TIMEOUT_SECONDS = 90

# Number of attempts for each call. A single retry handles transient
# timeouts and 5xx server errors without user-visible failure.
MAX_ATTEMPTS = 2

# Backoff between retries, in seconds.
RETRY_BACKOFF_SECONDS = 1.5


# ---------------------------------------------------------------------------
# Domain model
# ---------------------------------------------------------------------------


@dataclass
class SanctionHit:
    """A single sanctions record returned by Dilisense, normalized for UI use."""

    dilisense_id: str
    name: str
    source_id: str
    source_type: str
    sanction_details: list = field(default_factory=list)
    list_date_ms: int | None = None
    alias_names: list = field(default_factory=list)
    addresses: list = field(default_factory=list)
    other_information: list = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def list_date(self):
        if not self.list_date_ms:
            return None
        try:
            return datetime.fromtimestamp(self.list_date_ms / 1000, tz=timezone.utc)
        except (OSError, ValueError, TypeError):
            return None

    @property
    def primary_program(self) -> str:
        """Best-guess program identifier, e.g. 'CMIC-EO13959'."""
        return self.sanction_details[0] if self.sanction_details else ""


@dataclass
class ScreeningResult:
    """Aggregated result for one entity screened against Dilisense."""

    isin: str
    primary_name: str
    queried_names: list
    hits: list = field(default_factory=list)
    error: str | None = None
    response_time_ms: float = 0.0
    attempt_count: int = 1

    @property
    def is_flagged(self) -> bool:
        return len(self.hits) > 0

    @property
    def hit_count(self) -> int:
        return len(self.hits)

    @property
    def unique_source_ids(self) -> list:
        return sorted({h.source_id for h in self.hits})


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class DilisenseError(RuntimeError):
    """Base exception for Dilisense API errors."""


class DilisenseAuthError(DilisenseError):
    """401 Unauthorized."""


class DilisenseQuotaError(DilisenseError):
    """429 Rate limit / quota exceeded."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class DilisenseClient:
    """
    Minimal client for the Dilisense AML Screening API.

    Retries automatically on transient failures:
      - ReadTimeout / ConnectionError
      - 5xx server errors ("internal server error occurred")
    """

    def __init__(self, api_key: str, base_url: str = DILISENSE_BASE_URL) -> None:
        if not api_key:
            raise ValueError("Dilisense API key must not be empty.")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update({"x-api-key": api_key})

    # -- low-level -----------------------------------------------------------

    def _get(self, endpoint: str, params: dict | None = None) -> tuple[dict, int]:
        """GET with retry. Returns (json_payload, attempt_count)."""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"

        last_exc: Exception | None = None
        last_resp: requests.Response | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = self._session.get(
                    url, params=params or {}, timeout=REQUEST_TIMEOUT_SECONDS
                )
                last_resp = resp
            except requests.exceptions.ReadTimeout as exc:
                last_exc = exc
                if attempt < MAX_ATTEMPTS:
                    time.sleep(RETRY_BACKOFF_SECONDS)
                    continue
                raise DilisenseError(
                    f"Dilisense did not respond within {REQUEST_TIMEOUT_SECONDS} seconds "
                    f"after {MAX_ATTEMPTS} attempts. Try again in a moment."
                ) from exc
            except requests.exceptions.ConnectionError as exc:
                last_exc = exc
                if attempt < MAX_ATTEMPTS:
                    time.sleep(RETRY_BACKOFF_SECONDS)
                    continue
                raise DilisenseError(f"Connection error after {MAX_ATTEMPTS} attempts: {exc}") from exc

            # Non-retryable client errors
            if resp.status_code == 401:
                raise DilisenseAuthError("401 Unauthorized — API key invalid or not activated.")
            if resp.status_code == 429:
                raise DilisenseQuotaError("429 Quota exceeded for this API key.")

            # Retryable server errors (500, 502, 503, 504 ...)
            if resp.status_code >= 500:
                if attempt < MAX_ATTEMPTS:
                    time.sleep(RETRY_BACKOFF_SECONDS)
                    continue
                raise DilisenseError(
                    f"Dilisense server error ({resp.status_code}) after {MAX_ATTEMPTS} attempts. "
                    f"Response: {resp.text[:200]}"
                )

            # 2xx / 3xx / 4xx (non-401, non-429) — let the caller handle it
            resp.raise_for_status()
            return resp.json(), attempt

        # Fallback — should be unreachable
        if last_resp is not None:
            last_resp.raise_for_status()
        raise DilisenseError("Request failed without a response.") from last_exc

    # -- high-level ----------------------------------------------------------

    def check_entity(
        self,
        isin: str,
        primary_name: str,
        query_names: Iterable,
        *,
        fuzzy_search: int = 2,
    ) -> ScreeningResult:
        """Screen one entity against Dilisense."""
        names = [n for n in query_names if n and n.strip()]
        if not names:
            return ScreeningResult(
                isin=isin, primary_name=primary_name, queried_names=[],
                error="No query names provided.",
            )

        params = {"names": ",".join(names), "fuzzy_search": fuzzy_search}

        t0 = _now_ms()
        attempts = 1
        try:
            payload, attempts = self._get("checkEntity", params=params)
        except DilisenseError as exc:
            return ScreeningResult(
                isin=isin, primary_name=primary_name, queried_names=names,
                error=str(exc), response_time_ms=_now_ms() - t0,
                attempt_count=attempts,
            )

        hits = _parse_hits(payload)
        seen, unique = set(), []
        for h in hits:
            if h.dilisense_id and h.dilisense_id not in seen:
                seen.add(h.dilisense_id)
                unique.append(h)

        return ScreeningResult(
            isin=isin,
            primary_name=primary_name,
            queried_names=names,
            hits=unique,
            response_time_ms=_now_ms() - t0,
            attempt_count=attempts,
        )

    def get_source_list(self) -> list:
        """Return the list of sources covered by Dilisense (1 API call)."""
        payload, _ = self._get("getSourceList")
        for key in ("sources", "source_list", "lists", "data"):
            if key in payload and isinstance(payload[key], list):
                return payload[key]
        if isinstance(payload, list):
            return payload
        return []


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _parse_hits(payload: dict) -> list:
    records = payload.get("found_records") or payload.get("hits") or []
    out = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        list_date_raw = rec.get("list_date")
        try:
            list_date_ms = int(list_date_raw) if list_date_raw is not None else None
        except (TypeError, ValueError):
            list_date_ms = None

        out.append(SanctionHit(
            dilisense_id=str(rec.get("id", "")),
            name=str(rec.get("name", "")),
            source_id=str(rec.get("source_id", "") or rec.get("sourceId", "")),
            source_type=str(rec.get("source_type", "") or rec.get("sourceType", "")),
            sanction_details=list(rec.get("sanction_details", []) or []),
            list_date_ms=list_date_ms,
            alias_names=list(rec.get("alias_names", []) or []),
            addresses=list(rec.get("address", []) or []),
            other_information=list(rec.get("other_information", []) or []),
            raw=rec,
        ))
    return out


def _now_ms() -> float:
    return time.perf_counter() * 1000
