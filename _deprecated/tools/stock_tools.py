"""
ReadyTrader Stocks tools for Agent Zero.
Wraps the ReadyTrader-Stocks MCP server to give agents access to
stock market data, fundamental analysis, technical analysis, and trading.
"""
import json
from urllib.parse import urlparse

import httpx

from python.helpers.tool import Tool, Response
from python.helpers.plugins import get_plugin_config


_BLOCKED_HOSTS = {
    "169.254.169.254",           # AWS/GCP IMDS
    "metadata.google.internal",  # GCP metadata
    "metadata.azure.com",        # Azure IMDS
}
_DEFAULT_URL = "http://localhost:8000"
_STRATEGY_CODE_LIMIT = 20000


def _validate_url(url: str):
    """Validate an MCP server URL. Returns (safe_url, warning_or_None)."""
    try:
        parsed = urlparse(url)
    except Exception:
        return _DEFAULT_URL, f"Invalid mcp_server_url ({url!r}); falling back to {_DEFAULT_URL}."
    if parsed.scheme not in ("http", "https"):
        return _DEFAULT_URL, f"mcp_server_url must use http or https; falling back to {_DEFAULT_URL}."
    host = (parsed.hostname or "").lower()
    if not host:
        return _DEFAULT_URL, f"mcp_server_url has no host; falling back to {_DEFAULT_URL}."
    if host in _BLOCKED_HOSTS:
        return _DEFAULT_URL, (
            f"mcp_server_url host {host!r} is blocked (SSRF protection); "
            f"falling back to {_DEFAULT_URL}."
        )
    return url, None


def _cfg(agent) -> dict:
    defaults = {
        "mcp_server_url": _DEFAULT_URL,
        "trading_mode": "paper",
        "default_market": "US",
        "default_timeframe": "1d",
        "max_position_size_usd": 1000.0,
        "mcp_request_timeout": 30,
    }
    cfg = get_plugin_config("readytrader_stocks", agent=agent) or {}
    for k, v in defaults.items():
        cfg.setdefault(k, v)
    safe_url, warning = _validate_url(cfg["mcp_server_url"])
    cfg["mcp_server_url"] = safe_url
    if warning:
        cfg["_ssrf_warning"] = warning
    return cfg


async def _call_mcp(base_url: str, tool_name: str, args: dict, timeout: float = 30) -> dict:
    """Call the MCP server. Returns a dict with either the server response
    or a structured offline error. Never raises for connection errors."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{base_url}/mcp/call_tool",
                json={"name": tool_name, "arguments": args},
            )
            resp.raise_for_status()
            return resp.json()
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        return {
            "error": "mcp_unreachable",
            "message": (
                f"MCP server unreachable at {base_url}. "
                "Start ReadyTrader-Stocks MCP server per README."
            ),
            "detail": str(e),
        }


def _format(result: dict, warning: str | None) -> str:
    body = json.dumps(result, indent=2)
    if warning:
        return f"[WARNING] {warning}\n{body}"
    return body


class GetStockQuote(Tool):
    """Fetch the current quote for a stock ticker."""

    async def execute(self, **kwargs) -> Response:
        cfg = _cfg(self.agent)
        symbol = self.args.get("symbol", "AAPL")
        try:
            result = await _call_mcp(
                cfg["mcp_server_url"],
                "get_stock_price",
                {"symbol": symbol, "mode": cfg["trading_mode"]},
                timeout=cfg["mcp_request_timeout"],
            )
            return Response(message=_format(result, cfg.get("_ssrf_warning")), break_loop=False)
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
                {"symbol": symbol, "timeframe": timeframe, "limit": limit, "mode": cfg["trading_mode"]},
                timeout=cfg["mcp_request_timeout"],
            )
            return Response(message=_format(result, cfg.get("_ssrf_warning")), break_loop=False)
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
                timeout=cfg["mcp_request_timeout"],
            )
            return Response(message=_format(result, cfg.get("_ssrf_warning")), break_loop=False)
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
                timeout=cfg["mcp_request_timeout"],
            )
            return Response(message=_format(result, cfg.get("_ssrf_warning")), break_loop=False)
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
                timeout=cfg["mcp_request_timeout"],
            )
            return Response(message=_format(result, cfg.get("_ssrf_warning")), break_loop=False)
        except Exception as e:
            return Response(message=f"Error fetching regime: {e}", break_loop=False)


class RunStockBacktest(Tool):
    """Run a backtest simulation on a stock trading strategy."""

    async def execute(self, **kwargs) -> Response:
        cfg = _cfg(self.agent)
        strategy_code = self.args.get("strategy_code", "") or ""
        if len(strategy_code) > _STRATEGY_CODE_LIMIT:
            return Response(
                message=(
                    f"strategy_code too large ({len(strategy_code)} chars, "
                    f"max {_STRATEGY_CODE_LIMIT}). Shrink the strategy or split it."
                ),
                break_loop=False,
            )
        try:
            result = await _call_mcp(
                cfg["mcp_server_url"],
                "run_backtest_simulation",
                {
                    "strategy_code": strategy_code,
                    "symbol": self.args.get("symbol", "AAPL"),
                    "timeframe": self.args.get("timeframe", cfg["default_timeframe"]),
                    "mode": cfg["trading_mode"],
                },
                timeout=cfg["mcp_request_timeout"],
            )
            return Response(message=_format(result, cfg.get("_ssrf_warning")), break_loop=False)
        except Exception as e:
            return Response(message=f"Error running backtest: {e}", break_loop=False)
