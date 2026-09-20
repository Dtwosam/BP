from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy import Connection

from bp_engine.features.sources import FeatureSourceReader
from bp_engine.features.v4_models import BTCStateObservation


class V4FeatureSourceReader:
    def __init__(self, *, state_fresh_seconds: float = 10.0) -> None:
        self._reader = FeatureSourceReader(state_fresh_seconds=state_fresh_seconds)

    def latest_btc_state(
        self,
        connection: Connection,
        *,
        source: str,
        stream: str,
        instrument: str,
        as_of,
    ) -> BTCStateObservation | None:
        observation = self._reader.latest_state(
            connection,
            source=source,
            stream=stream,
            instrument=instrument,
            feature_at=as_of,
        )
        if observation is None:
            return None

        raw_price = observation.state.get("last_price")
        if raw_price in (None, ""):
            return None
        try:
            price = Decimal(str(raw_price))
        except (InvalidOperation, ValueError):
            return None
        if not price.is_finite() or price <= 0:
            return None

        return BTCStateObservation(
            row_id=observation.row_id,
            bucket_at=observation.bucket_at,
            last_event_at=observation.last_event_at,
            source=observation.source,
            stream=observation.stream,
            instrument=observation.instrument,
            price=price,
            state=dict(observation.state),
            fresh=observation.fresh,
            age_seconds=observation.age_seconds,
        )
