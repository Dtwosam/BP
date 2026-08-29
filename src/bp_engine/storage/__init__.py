"""Storage primitives for BP."""

from bp_engine.storage import schema as schema
from bp_engine.storage.improvement_schema import (
    improvement_evaluations,
    improvement_experiments,
    improvement_promotion_decisions,
)
from bp_engine.storage.live_readiness_schema import (
    live_order_events,
    live_order_intents,
    live_readiness_checks,
    live_reconciliation_runs,
    live_risk_decisions,
)
from bp_engine.storage.paper_schema import (
    paper_fills,
    paper_order_terminal_events,
    paper_orders,
    paper_settlements,
)

schema.paper_orders = paper_orders
schema.paper_fills = paper_fills
schema.paper_order_terminal_events = paper_order_terminal_events
schema.paper_settlements = paper_settlements
schema.improvement_experiments = improvement_experiments
schema.improvement_evaluations = improvement_evaluations
schema.improvement_promotion_decisions = improvement_promotion_decisions
schema.live_readiness_checks = live_readiness_checks
schema.live_risk_decisions = live_risk_decisions
schema.live_order_intents = live_order_intents
schema.live_order_events = live_order_events
schema.live_reconciliation_runs = live_reconciliation_runs
