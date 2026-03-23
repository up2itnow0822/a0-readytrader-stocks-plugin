"""
ReadyTrader Stocks tools for Agent Zero.
Wraps the ReadyTrader-Stocks MCP server to give agents access to
stock market data, fundamental analysis, technical analysis, and trading.
"""
import json

import httpx

from python.helpers.tool import Tool, Response
from python.helpers.plugins import get_plugin_config


def _cfg(agent) -> dict:
    defaults = {
        "mcp_server_url": "http://localhost:8000",
        "trading_mode": "paper",
        "default_market": "US",
        "default_timeframe": "1d",
        "max_position_size_usd": 1000.0,
    }
    cfg = get_plugin_config("readytrader-stocks", agent=agent) or {}
    for k, v in defaults.items():
        cfg.setdefault(k, v)
    return cfg


async def _call_mcp(base_url: str, tool_name: str, args: dict) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{base_url}/mcp/call_tool",
            json={"name": tool_name, "arguments": args},
        )
        resp.raise_for_status()
        return resp.json()


class GetStockQuote(Tool):
    """Fetch the current quote for a stock ticker."""

    async def execute(self, **kwargs) -> Response:
        cfg = _cfg(self.agent)
        symbol = self.args.get("symbol", "AAPL")
        try:
            result = await _call_mcp(
                cfg["mcp_server_url"],
                "get_stock_price",
                {"symbol": symbol},
            )
            return Response(message=json.dumps(result, indent=2), break_loop=False)
        except Exception as e:
            return Response(message=f"Error fetching quote: {e}", break_loop=False)


class FetchStockOHLCV(Tool):
    """Fetch OHLCV candle data for a stock."""

    async def execute(self, **kwargs) -> Response:
        cfg = _cfg(self.agent)
        symbol = self.args.get("symbol", "AAPL")
        timeframe = self.args.get("timeframe", cfg["default_timeframe"])
        limit = int(self.args.get("limit", 30))
        try:
            result = await _call_mcp(
                cfg["mcp_server_url"],
                "fetch_ohlcv",
                {"symbol": symbol, "timeframe": timeframe, "limit": limit},
            )
            return Response(message=json.dumps(result, indent=2), break_loop=False)
        except Exception as e:
            return Response(message=f"Error fetching OHLCV: {e}", break_loop=False)


class GetStockSentiment(Tool):
    """Get market sentiment for a stock from news and social sources."""

    async def execute(self, **kwargs) -> Response:
        cfg = _cfg(self.agent)
        symbol = self.args.get("symbol", "AAPL")
        try:
            result = await _call_mcp(
                cfg["mcp_server_url"],
                "get_market_sentiment",
                {"symbol": symbol},
            )
            return Response(message=json.dumps(result, indent=2), break_loop=False)
        except Exception as e:
            return Response(message=f"Error fetching sentiment: {e}", break_loop=False)


class GetFundamentalData(Tool):
    """Get fundamental analysis data (earnings, ratios, financials) for a stock."""

    async def execute(self, **kwargs) -> Response:
        cfg = _cfg(self.agent)
        symbol = self.args.get("symbol", "AAPL")
        try:
            result = await _call_mcp(
                cfg["mcp_server_url"],
                "get_fundamental_data",
                {"symbol": symbol},
            )
            return Response(message=json.dumps(result, indent=2), break_loop=False)
        except Exception as e:
            return Response(message=f"Error fetching fundamentals: {e}", break_loop=False)


class GetStockRegime(Tool):
    """Detect the current market regime for a stock (trending, ranging, volatile)."""

    async def execute(self, **kwargs) -> Response:
        cfg = _cfg(self.agent)
        symbol = self.args.get("symbol", "AAPL")
        try:
            result = await _call_mcp(
                cfg["mcp_server_url"],
                "get_market_regime",
                {"symbol": symbol},
            )
            return Response(message=json.dumps(result, indent=2), break_loop=False)
        except Exception as e:
            return Response(message=f"Error fetching regime: {e}", break_loop=False)


class RunStockBacktest(Tool):
    """Run a backtest simulation on a stock trading strategy."""

    async def execute(self, **kwargs) -> Response:
        cfg = _cfg(self.agent)
        try:
            result = await _call_mcp(
                cfg["mcp_server_url"],
                "run_backtest_simulation",
                {
                    "strategy_code": self.args.get("strategy_code", ""),
                    "symbol": self.args.get("symbol", "AAPL"),
                    "timeframe": self.args.get("timeframe", cfg["default_timeframe"]),
                },
            )
            return Response(message=json.dumps(result, indent=2), break_loop=False)
        except Exception as e:
            return Response(message=f"Error running backtest: {e}", break_loop=False)
