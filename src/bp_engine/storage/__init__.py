"""Storage primitives for BP."""

from bp_engine.storage import schema as schema
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
