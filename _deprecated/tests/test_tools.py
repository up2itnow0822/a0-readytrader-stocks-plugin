"""Tests for the ReadyTrader Stocks tool classes.

Strategy:
- Patch httpx.AsyncClient inside tools.stock_tools so we never touch the network.
- Exercise each Tool.execute() for happy path, MCP offline, config URL override,
  and (for RunStockBacktest) the strategy_code size limit.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import tools.stock_tools as st


# ---------------------------------------------------------------------------
# httpx mocking helpers
# ---------------------------------------------------------------------------

def _mock_httpx_success(payload: dict):
    """Return a patched AsyncClient factory whose .post() returns payload."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=payload)

    client = MagicMock()
    client.post = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    return MagicMock(return_value=client), client


def _mock_httpx_connect_error():
    client = MagicMock()
    client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=client), client


def _mock_httpx_timeout():
    client = MagicMock()
    client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=client), client


# ---------------------------------------------------------------------------
# _cfg / _validate_url
# ---------------------------------------------------------------------------

class TestConfig:
    def test_defaults_applied(self, set_plugin_config, mock_agent):
        set_plugin_config()
        cfg = st._cfg(mock_agent)
        assert cfg["mcp_server_url"] == "http://localhost:8000"
        assert cfg["trading_mode"] == "paper"
        assert cfg["mcp_request_timeout"] == 30
        assert "_ssrf_warning" not in cfg

    def test_override_url_respected(self, set_plugin_config, mock_agent):
        set_plugin_config(mcp_server_url="http://example.com:9000")
        cfg = st._cfg(mock_agent)
        assert cfg["mcp_server_url"] == "http://example.com:9000"
        assert "_ssrf_warning" not in cfg

    def test_override_timeout(self, set_plugin_config, mock_agent):
        set_plugin_config(mcp_request_timeout=5)
        cfg = st._cfg(mock_agent)
        assert cfg["mcp_request_timeout"] == 5

    @pytest.mark.parametrize("blocked", [
        "http://169.254.169.254/",
        "http://metadata.google.internal/",
        "http://metadata.azure.com/",
    ])
    def test_ssrf_blocked_hosts(self, set_plugin_config, mock_agent, blocked):
        set_plugin_config(mcp_server_url=blocked)
        cfg = st._cfg(mock_agent)
        assert cfg["mcp_server_url"] == "http://localhost:8000"
        assert "_ssrf_warning" in cfg
        assert "blocked" in cfg["_ssrf_warning"]

    def test_ssrf_bad_scheme(self, set_plugin_config, mock_agent):
        set_plugin_config(mcp_server_url="file:///etc/passwd")
        cfg = st._cfg(mock_agent)
        assert cfg["mcp_server_url"] == "http://localhost:8000"
        assert "_ssrf_warning" in cfg


# ---------------------------------------------------------------------------
# Per-tool tests
# ---------------------------------------------------------------------------

def _make(tool_cls, agent, **args):
    return tool_cls(agent=agent, args=args)


TOOL_CASES = [
    (st.GetStockQuote,      {"symbol": "AAPL"},           "get_stock_price"),
    (st.FetchStockOHLCV,    {"symbol": "AAPL"},           "fetch_ohlcv"),
    (st.GetStockSentiment,  {"symbol": "AAPL"},           "get_market_sentiment"),
    (st.GetFundamentalData, {"symbol": "AAPL"},           "get_fundamental_data"),
    (st.GetStockRegime,     {"symbol": "AAPL"},           "get_market_regime"),
    (st.RunStockBacktest,   {"strategy_code": "pass"},    "run_backtest_simulation"),
]


class TestToolsSuccess:
    @pytest.mark.parametrize("tool_cls,args,expected_mcp_name", TOOL_CASES)
    async def test_success(self, set_plugin_config, mock_agent,
                            tool_cls, args, expected_mcp_name):
        set_plugin_config()
        payload = {"ok": True, "tool": expected_mcp_name}
        factory, client = _mock_httpx_success(payload)
        with patch.object(st.httpx, "AsyncClient", factory):
            tool = _make(tool_cls, mock_agent, **args)
            response = await tool.execute()
        assert response.break_loop is False
        parsed = json.loads(response.message)
        assert parsed == payload
        # Verify correct MCP endpoint was called
        client.post.assert_awaited_once()
        call = client.post.await_args
        assert call.args[0].endswith("/mcp/call_tool")
        assert call.kwargs["json"]["name"] == expected_mcp_name


class TestToolsOffline:
    @pytest.mark.parametrize("tool_cls,args,expected_mcp_name", TOOL_CASES)
    async def test_connect_error(self, set_plugin_config, mock_agent,
                                  tool_cls, args, expected_mcp_name):
        set_plugin_config()
        factory, _ = _mock_httpx_connect_error()
        with patch.object(st.httpx, "AsyncClient", factory):
            tool = _make(tool_cls, mock_agent, **args)
            response = await tool.execute()
        assert response.break_loop is False
        parsed = json.loads(response.message)
        assert parsed["error"] == "mcp_unreachable"
        assert "MCP server unreachable" in parsed["message"]
        assert "ReadyTrader-Stocks" in parsed["message"]

    async def test_timeout_error(self, set_plugin_config, mock_agent):
        set_plugin_config()
        factory, _ = _mock_httpx_timeout()
        with patch.object(st.httpx, "AsyncClient", factory):
            tool = _make(st.GetStockQuote, mock_agent, symbol="AAPL")
            response = await tool.execute()
        parsed = json.loads(response.message)
        assert parsed["error"] == "mcp_unreachable"


class TestConfigOverrideRespected:
    async def test_url_override_is_used_in_request(self, set_plugin_config, mock_agent):
        set_plugin_config(mcp_server_url="http://stocks.example.com:1234")
        factory, client = _mock_httpx_success({"ok": True})
        with patch.object(st.httpx, "AsyncClient", factory):
            tool = _make(st.GetStockQuote, mock_agent, symbol="MSFT")
            await tool.execute()
        call = client.post.await_args
        assert call.args[0].startswith("http://stocks.example.com:1234")

    async def test_trading_mode_forwarded(self, set_plugin_config, mock_agent):
        set_plugin_config(trading_mode="live")
        factory, client = _mock_httpx_success({"ok": True})
        with patch.object(st.httpx, "AsyncClient", factory):
            tool = _make(st.RunStockBacktest, mock_agent, strategy_code="pass")
            await tool.execute()
        payload = client.post.await_args.kwargs["json"]
        assert payload["arguments"]["mode"] == "live"

    async def test_ssrf_warning_prepended(self, set_plugin_config, mock_agent):
        set_plugin_config(mcp_server_url="http://169.254.169.254/")
        factory, _ = _mock_httpx_success({"ok": True})
        with patch.object(st.httpx, "AsyncClient", factory):
            tool = _make(st.GetStockQuote, mock_agent, symbol="AAPL")
            response = await tool.execute()
        assert response.message.startswith("[WARNING]")
        assert "SSRF" in response.message


class TestStrategyCodeLimit:
    async def test_exceeds_limit_rejected(self, set_plugin_config, mock_agent):
        set_plugin_config()
        big = "x" * (st._STRATEGY_CODE_LIMIT + 1)
        tool = _make(st.RunStockBacktest, mock_agent, strategy_code=big)
        response = await tool.execute()
        assert response.break_loop is False
        assert "too large" in response.message

    async def test_under_limit_accepted(self, set_plugin_config, mock_agent):
        set_plugin_config()
        factory, client = _mock_httpx_success({"ok": True})
        with patch.object(st.httpx, "AsyncClient", factory):
            tool = _make(st.RunStockBacktest, mock_agent, strategy_code="pass")
            response = await tool.execute()
        assert json.loads(response.message) == {"ok": True}
