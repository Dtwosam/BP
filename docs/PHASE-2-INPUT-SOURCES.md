# Phase 2 external input contracts

Checked 20 August 2026 against official documentation.

## Polymarket CLOB market WebSocket

- Endpoint path: `/ws/market` on the Polymarket CLOB WebSocket service.
- Initial subscription uses `type: "market"` plus `assets_ids` for the Up/Down CLOB token IDs discovered by Phase 1.
- Relevant events include `book`, `price_change`, `last_trade_price`, `tick_size_change`, and optionally custom-feature best-bid/ask/lifecycle events.
- Client heartbeat: send `PING` every 10 seconds and expect `PONG`.
- The recorder must preserve source timestamp plus local receive timestamp.

Official reference: https://docs.polymarket.com/api-reference/wss/market

## Bybit V5 public WebSockets — primary BTC venue

Mainnet endpoints:

- Spot: `wss://stream.bybit.com/v5/public/spot`
- Linear perpetual/futures: `wss://stream.bybit.com/v5/public/linear`

Initial BTC topics:

- `orderbook.50.BTCUSDT`
- `publicTrade.BTCUSDT`
- later derivatives ticker/open-interest/liquidation topics where useful and verified

Order-book rules:

- first message is a snapshot;
- delta amount `0` deletes a level;
- new snapshots reset local book state;
- `u` is update ID and `seq` is cross sequence;
- `cts` is the matching-engine timestamp and can be correlated with trade `T`.

Connection guidance:

- send a ping heartbeat every 20 seconds;
- reconnect and re-subscribe on disconnect;
- a new snapshot after reconnect resets the local book.

Official references:

- https://bybit-exchange.github.io/docs/v5/ws/connect
- https://bybit-exchange.github.io/docs/v5/websocket/public/orderbook
- https://bybit-exchange.github.io/docs/v5/websocket/public/trade

## Coinbase Advanced Trade — initial secondary candidate

- Market data endpoint: `wss://advanced-trade-ws.coinbase.com`
- Public channels include `level2`, `market_trades`, `ticker`, and `heartbeats`.
- `level2` is documented as guaranteeing delivery of all updates for keeping a synchronized book.
- Subscribe to heartbeats to keep subscriptions open during sparse periods.

Official references:

- https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/websocket/websocket-channels
- https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/guides/websocket

These are external API facts, not permanent assumptions. Recheck when integration behavior changes.
