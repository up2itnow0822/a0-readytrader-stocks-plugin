# ReadyTrader Stocks — Agent Zero Plugin

An Agent Zero plugin that connects your agent to the [ReadyTrader-Stocks](https://github.com/up2itnow0822/ReadyTrader-Stocks) MCP server. Your agent gets stock quotes, fundamental data, sentiment, technical analysis, and backtesting through a running ReadyTrader-Stocks instance.

## What it does

- **Stock quotes** — live prices for any US-listed ticker
- **OHLCV data** — historical candle data at any timeframe
- **Fundamental data** — earnings, financial ratios, key metrics
- **Sentiment** — news and social sentiment for individual stocks
- **Backtest** — test trading strategies against historical data
- **Regime detection** — is the stock trending, ranging, or volatile?

## Setup

1. Install and run the [ReadyTrader-Stocks](https://github.com/up2itnow0822/ReadyTrader-Stocks) MCP server
2. Drop this plugin into your Agent Zero plugins directory
3. Configure the MCP server URL in Settings → Agent → ReadyTrader Stocks

Defaults to paper trading. Switch to live when ready.

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `mcp_server_url` | `http://localhost:8000` | ReadyTrader-Stocks server address |
| `trading_mode` | `paper` | `paper` or `live` |
| `max_position_size_usd` | `1000` | Per-trade size cap |
| `max_portfolio_risk_pct` | `5.0` | Max portfolio risk percentage |
| `stop_loss_pct` | `2.0` | Default stop loss percentage |

## License

MIT
