-- Phase 12 causal replay indexes.
--
-- These indexes are intentionally partial so the continuously growing raw event
-- ledger pays index-write cost only for the Polymarket market events consumed by
-- the paper execution replay path. CREATE INDEX CONCURRENTLY must be executed
-- outside a transaction on production hosts.

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_raw_market_events_pm_book_replay_anchor
ON raw_market_events (instrument, asset_id, received_at DESC, id DESC)
WHERE source = 'polymarket'
  AND stream = 'market'
  AND event_type = 'book';

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_raw_market_events_pm_price_change_replay
ON raw_market_events (instrument, received_at, id)
WHERE source = 'polymarket'
  AND stream = 'market'
  AND event_type = 'price_change';
