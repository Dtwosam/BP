from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from bp_engine.execution.models import (
    ExecutionCancelAck,
    ExecutionOrderAck,
    ExecutionOrderRequest,
)


@runtime_checkable
class ExecutionGateway(Protocol):
    """Execution boundary shared by paper execution and a later live adapter."""

    def submit_order(self, request: ExecutionOrderRequest) -> ExecutionOrderAck: ...

    def cancel_order(self, order_id: str, observed_at: datetime) -> ExecutionCancelAck: ...
