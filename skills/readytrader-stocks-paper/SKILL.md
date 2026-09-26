---
name: readytrader-stocks-paper
description: "Paper stock trading through the ReadyTrader-Stocks MCP tools: quote, risk check, paper order. Never live."
version: 2.0.0
author: Bill Wilson (up2itnow0822)
tags: ["trading", "stocks", "equities", "paper-trading", "readytrader", "mcp"]
trigger_patterns:
  - paper trade stock
  - paper buy shares
  - stock price readytrader
  - readytrader stocks
  - stock risk check
  - paper trading stocks
---

# ReadyTrader-Stocks paper trading

The ReadyTrader Stocks plugin registers the ReadyTrader-Stocks MCP server as `readytrader_stocks`, always
under that name. Its tools are called as `readytrader_stocks.<tool>` with `tool_args`, for example:

```json
{"tool_name": "readytrader_stocks.get_stock_price", "tool_args": {"symbol": "AAPL"}}
```

The server runs the paper profile only: paper mode, trading halted, live execution disabled, no
broker keys. Every order is simulated against a paper wallet kept in the plugin folder.

## Rules

- US stock tickers (`AAPL`, `MSFT`, ...). Share amounts, not dollars, go into orders.
- Never try to enable live trading, change the server's environment, or reset the paper wallet
  (`readytrader_stocks.reset_paper_wallet`) unless the user asks for a reset. If the user asks for a
  live trade, refuse: live trading is an operator process outside this plugin.
- Read every answer's `ok` first. `ok: false` carries `error.code` and `error.message`; report them.

## Procedure

1. Quote: `readytrader_stocks.get_stock_price` with `{"symbol": "AAPL"}`; the price is `data.last`.
   It sizes the order; it is not sent with it. `market_data_error` means there is no quote for that
   ticker right now.
2. Paper funds: there is no balance tool. `readytrader_stocks.deposit_paper_funds` with
   `{"asset": "USD", "amount": 10000}` adds paper cash, and its `data.message` states the new cash
   balance. Deposit when the user asks for paper funds, or after an order answers `insufficient_funds`
   (its message states the cash the wallet has) and the user agrees.
3. Risk check: `readytrader_stocks.validate_trade_risk` with `{"side": "buy", "symbol": "AAPL",
   "amount_usd": <shares x price>, "portfolio_value": <wallet value>}`, where the wallet value is its
   cash plus the shares you know it holds at their current price. The server takes your numbers for
   this check, so give your best figures, never larger ones. Proceed only when `data.result.allowed`
   is `true`; otherwise report `data.result.reason` and stop. When `data.result.needs_confirmation`
   is `true` (a large order), ask the user before placing it. `ok: true` only means the check ran.
   Each order may be at most 5% of the portfolio. The limit applies to each order, not to the
   position: several orders can build a larger position, so tell the user when one would, and never
   split an order to get past a refusal.
4. Order: `readytrader_stocks.place_market_order` with `{"symbol": "AAPL", "side": "buy", "amount":
   <shares>}`, with no `price` (the tool takes none; the server fills at its market price). The order
   repeats the risk check against the wallet's real value. Done when `ok` is `true` and `data.venue`
   is `"paper"`; report `data.result`, which states the fill price.
5. A refusal ends the attempt; report it, do not retry with other numbers:
   `risk_blocked` (the risk rules refused, including "No market price for ..." when the order cannot
   be valued; `error.message` says why), `insufficient_funds`, `invalid_request`, `execution_error`.

## Good to know

- `readytrader_stocks.place_limit_order` fills only a marketable limit (a buy at or above the market);
  otherwise it answers `limit_not_marketable`, because paper mode keeps no resting orders.
- The server does not score sentiment. You may pass your own reading as `sentiment_score` (-1 to +1)
  to the risk check and the order; left out, it is neutral.
- `get_social_sentiment`, `get_market_news` and `get_financial_news` need provider keys, which this
  plugin never passes to the server, so here they always answer `not_configured`. `fetch_rss_news`
  needs no key. `get_market_sentiment` reads a public web page and answered `source_unavailable`
  (HTTP 404) when this plugin was tested in September 2026.
- `deposit_paper_funds` also takes shares (`{"asset": "AAPL", ...}`), valued at the market price; it
  answers `paper_price_required` when there is no price for them.
- `start_brokerage_private_ws` answers `paper_mode_not_supported`: paper orders fill at once and
  never rest at a broker.
- `run_backtest_simulation` and `run_synthetic_stress_test` execute the strategy code you pass;
  review it first.
