from bp_engine.execution.models import (
    PAPER_EXECUTION_VERSION,
    ExecutionCancelAck,
    ExecutionOrderAck,
    ExecutionOrderRequest,
    PaperExecutionConfig,
)
from bp_engine.execution.protocol import ExecutionGateway

__all__ = [
    "PAPER_EXECUTION_VERSION",
    "ExecutionCancelAck",
    "ExecutionGateway",
    "ExecutionOrderAck",
    "ExecutionOrderRequest",
    "InterlockDecision",
    "PaperExecutionConfig",
    "PolymarketLiveExecutionGateway",
]


def __getattr__(name: str) -> object:
    if name == "InterlockDecision":
        from bp_engine.execution.live import InterlockDecision

        return InterlockDecision
    if name == "PolymarketLiveExecutionGateway":
        from bp_engine.execution.live import PolymarketLiveExecutionGateway

        return PolymarketLiveExecutionGateway
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
