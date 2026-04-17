"""
Dilisense API client for the NaroIX Sanctions Screener.

Wraps the /checkEntity endpoint with clear error handling and
a compact response structure used throughout the app.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

import requests


DILISENSE_BASE_URL = "https://api.dilisense.com/v1"


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
    sanction_details: list[str] = field(default_factory=list)
    list_date_ms: int | None = None
    alias_names: list[str] = field(default_factory=list)
    addresses: list[str] = field(default_factory=list)
    other_information: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def list_date(self) -> datetime | None:
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
    queried_names: list[str]
    hits: list[SanctionHit] = field(default_factory=list)
    error: str | None = None
    response_time_ms: float = 0.0

    @property
    def is_flagged(self) -> bool:
        return len(self.hits) > 0

    @property
    def hit_count(self) -> int:
        return len(self.hits)

    @property
    def unique_source_ids(self) -> list[str]:
        return sorted({h.source_id for h in self.hits})


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class DilisenseError(RuntimeError):
    """Base exception for Dilisense API errors."""


class DilisenseAuthError(DilisenseError):
    """401 Unauthorized."""


class DilisenseQuotaError(DilisenseError):
    """429 Rate limit / quota exceeded."""


class DilisenseClient:
    """
    Minimal client for the Dilisense AML Screening API.

    Usage:
        client = DilisenseClient(api_key="...")
        result = client.check_entity("KYG8020E1199", "SMIC", ["SMIC", "Semiconductor..."])
    """

    def __init__(self, api_key: str, base_url: str = DILISENSE_BASE_URL) -> None:
        if not api_key:
            raise ValueError("Dilisense API key must not be empty.")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update({"x-api-key": api_key})

    # -- low-level -----------------------------------------------------------

    def _get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        resp = self._session.get(url, params=params or {}, timeout=30)
        if resp.status_code == 401:
            raise DilisenseAuthError("401 Unauthorized — API key invalid or not activated.")
        if resp.status_code == 429:
            raise DilisenseQuotaError("429 Quota exceeded for this API key.")
        if resp.status_code >= 500:
            raise DilisenseError(f"{resp.status_code} Server error: {resp.text[:200]}")
        resp.raise_for_status()
        return resp.json()

    # -- high-level ----------------------------------------------------------

    def check_entity(
        self,
        isin: str,
        primary_name: str,
        query_names: Iterable[str],
        *,
        fuzzy_search: int = 2,
    ) -> ScreeningResult:
        """
        Screen one entity against Dilisense.

        `query_names` is typically the primary name + all alias variants generated
        by matching.build_query_names().
        """
        names = [n for n in query_names if n and n.strip()]
        if not names:
            return ScreeningResult(
                isin=isin, primary_name=primary_name, queried_names=[],
                error="No query names provided.",
            )

        params = {"names": ",".join(names), "fuzzy_search": fuzzy_search}

        t0 = _now_ms()
        try:
            payload = self._get("checkEntity", params=params)
        except DilisenseError as exc:
            return ScreeningResult(
                isin=isin, primary_name=primary_name, queried_names=names,
                error=str(exc), response_time_ms=_now_ms() - t0,
            )

        hits = _parse_hits(payload)
        # Deduplicate by dilisense_id — batched queries can return duplicates
        # if the same record matches multiple query names.
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
        )

    def get_source_list(self) -> list[dict[str, Any]]:
        """Return the list of sources covered by Dilisense (1 API call)."""
        payload = self._get("getSourceList")
        # The response may wrap the list under several possible keys; be tolerant.
        for key in ("sources", "source_list", "lists", "data"):
            if key in payload and isinstance(payload[key], list):
                return payload[key]
        # Fallback: if the top level is already a list
        if isinstance(payload, list):
            return payload
        return []


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _parse_hits(payload: dict[str, Any]) -> list[SanctionHit]:
    records = payload.get("found_records") or payload.get("hits") or []
    out: list[SanctionHit] = []
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
    import time
    return time.perf_counter() * 1000
