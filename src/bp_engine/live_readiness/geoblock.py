from __future__ import annotations

from datetime import UTC, datetime

import httpx

from .models import GeoblockResult


class GeoblockError(RuntimeError):
    """Raised when geographic eligibility cannot be verified safely."""


class GeoblockClient:
    def __init__(
        self,
        *,
        url: str,
        timeout_seconds: float = 5.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not url.strip():
            raise ValueError("geoblock url must not be blank")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._url = url
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def check(self, *, observed_at: datetime | None = None) -> GeoblockResult:
        checked_at = observed_at or datetime.now(UTC)
        try:
            response = self._client.get(self._url)
        except httpx.HTTPError as exc:
            raise GeoblockError("geoblock check failed") from exc

        if response.status_code != httpx.codes.OK:
            raise GeoblockError("geoblock check returned a non-success status")

        try:
            payload = response.json()
        except ValueError as exc:
            raise GeoblockError("geoblock check returned invalid JSON") from exc

        if not isinstance(payload, dict):
            raise GeoblockError("geoblock check returned an invalid schema")

        blocked = payload.get("blocked")
        country = payload.get("country")
        region = payload.get("region")
        if type(blocked) is not bool or not isinstance(country, str) or not isinstance(region, str):
            raise GeoblockError("geoblock check returned an invalid schema")

        return GeoblockResult(
            blocked=blocked,
            country=country,
            region=region,
            checked_at=checked_at,
        )
