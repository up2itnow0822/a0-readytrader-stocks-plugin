"""The Agent Zero v2.13 plugin contract (plugin.yaml, settings screen, skill), the tool names and arguments the skill
uses, and the product constants in hooks.py, pinned against ReadyTrader-Stocks itself.

Sources mirrored here: agent-zero helpers/plugins.py (manifest fields), skills/a0-create-plugin/references/
implementation.md (settings_sections), plugins/AGENTS.md (config.html binds to config.* through
$store.pluginSettingsPrototype), helpers/skills.py validate_skill (name and description rules); ReadyTrader-Stocks
PR #4 (September 2026): its MCP tool schemas, the environment switches it reads, and its data-file variables.
"""

import importlib.util
import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "readytrader-stocks-paper" / "SKILL.md"


def load_hooks():
    spec = importlib.util.spec_from_file_location("hooks_contract", ROOT / "hooks.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

A0_SETTINGS_SECTIONS = {"agent", "external", "mcp", "developer", "backup"}
A0_MANIFEST_FIELDS = {"name", "title", "description", "version", "settings_sections", "per_project_config",
                      "per_agent_config", "always_enabled"}

# The 20 tools ReadyTrader-Stocks' app/main.py registers, with their parameters: the input schemas it lists over MCP
# stdio (PR #4 branch, listed live in the UAT). A tool or argument the skill names that is not here is one the agent
# cannot use.
SERVER_TOOLS = {
    "deposit_paper_funds": ("amount", "asset"),
    "fetch_ohlcv": ("limit", "symbol", "timeframe"),
    "fetch_rss_news": ("symbol",),
    "get_financial_news": ("symbol",),
    "get_latest_insights": ("symbol",),
    "get_market_news": ("symbol",),
    "get_market_regime": ("symbol", "timeframe"),
    "get_market_sentiment": (),
    "get_multiple_prices": ("symbols",),
    "get_social_sentiment": ("symbol",),
    "get_stock_price": ("symbol",),
    "place_limit_order": ("amount", "price", "rationale", "sentiment_score", "side", "symbol"),
    "place_market_order": ("amount", "rationale", "sentiment_score", "side", "symbol"),
    "place_stock_order": ("amount", "audit_context", "exchange", "order_type", "price", "rationale", "sentiment_score",
                          "side", "symbol"),
    "post_market_insight": ("agent_id", "confidence", "reasoning", "signal", "symbol", "ttl_seconds"),
    "reset_paper_wallet": (),
    "run_backtest_simulation": ("strategy_code", "symbol", "timeframe"),
    "run_synthetic_stress_test": ("config_json", "strategy_code"),
    "start_brokerage_private_ws": ("brokerage",),
    "validate_trade_risk": ("amount_usd", "portfolio_value", "sentiment_score", "side", "symbol"),
}
# snake_case words in the skill's code spans that are answer codes or fields, not tools
NOT_TOOLS = {"risk_blocked", "paper_price_required", "limit_not_marketable", "insufficient_funds", "invalid_request",
             "execution_error", "market_data_error", "not_configured", "source_unavailable", "paper_mode_not_supported",
             "tool_name", "tool_args", "amount_usd", "portfolio_value", "needs_confirmation", "sentiment_score",
             "readytrader_stocks"}

# What ReadyTrader-Stocks reads (app/core/config.py, execution/*, core/*): the switches that close the live path, and
# every broker client's paper/sandbox switch with the value that selects paper/sandbox. E*TRADE's is opt-in: unset
# means production. Schwab and Robinhood have none.
EXPECTED_PAPER_ENV = {
    "PAPER_MODE": "true",
    "LIVE_TRADING_ENABLED": "false",
    "TRADING_HALTED": "true",
    "EXECUTION_APPROVAL_MODE": "auto",
    "MARKET_GUARD_ENABLED": "true",
    "ALPACA_PAPER": "true",
    "TRADIER_SANDBOX": "true",
    "ETRADE_SANDBOX": "true",
}
# The data files the server writes (READYTRADER_<X>_DB_PATH, then <X>_DB_PATH).
EXPECTED_DB_FILES = ("PAPER", "AUDIT", "EXECUTION", "IDEMPOTENCY", "INSIGHT", "STRATEGY")
# The server's answer to a brokerage-account stream in paper mode (app/tools/trading.py, PR #4).
EXPECTED_GATE = [("start_brokerage_private_ws", {"brokerage": "alpaca"}, "paper_mode_not_supported")]


def manifest():
    return yaml.safe_load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))


def test_manifest_fields_name_and_sections():
    m = manifest()
    assert set(m) <= A0_MANIFEST_FIELDS
    assert re.fullmatch(r"[a-z0-9_]+", m["name"]) and not m["name"].startswith("_")
    assert m["name"] == "readytrader_stocks"
    assert set(m.get("settings_sections") or []) <= A0_SETTINGS_SECTIONS
    for key in ("title", "description", "version"):
        assert m[key]


def test_manifest_version_matches_changelog_and_skill():
    version = manifest()["version"]
    assert f"## [{version}]" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"version: {version}" in SKILL.read_text(encoding="utf-8")


def test_settings_screen_binds_to_config_only():
    html = (ROOT / "webui" / "config.html").read_text(encoding="utf-8")
    assert "$store.pluginSettings" not in html  # not a store Agent Zero v2.13 provides
    bound = set(re.findall(r"config\.([a-z_]+)", html))
    defaults = set(yaml.safe_load((ROOT / "default_config.yaml").read_text(encoding="utf-8")))
    assert bound == defaults, f"screen fields {bound} != default_config.yaml keys {defaults}"


def test_no_tool_files_left_for_agent_zero_to_misload():
    # v1 shipped tools/stock_tools.py, which failed to import in Agent Zero and fell back to Unknown.
    assert not (ROOT / "tools").exists()


def frontmatter():
    text = SKILL.read_text(encoding="utf-8")
    m = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
    assert m, "SKILL.md must open with a closed --- frontmatter fence"
    return yaml.safe_load(m.group(1)), text[m.end():]


def test_skill_frontmatter_meets_agent_zero_rules():
    fm, _ = frontmatter()
    name = fm["name"]
    assert re.fullmatch(r"[a-z0-9-]+", name) and 1 <= len(name) <= 64
    assert not name.startswith("-") and not name.endswith("-") and "--" not in name
    assert 0 < len(fm["description"]) <= 1024
    assert fm.get("trigger_patterns")


def test_skill_names_only_real_server_tools():
    _, body = frontmatter()
    qualified = set(re.findall(r"readytrader_stocks\.([a-z_]+)", body))
    spans = set(re.findall(r"`([a-z][a-z0-9_]*_[a-z0-9_]+)`", body))
    unknown = (qualified | (spans - NOT_TOOLS)) - set(SERVER_TOOLS)
    assert qualified and not unknown, f"not ReadyTrader-Stocks tools: {sorted(unknown)}"


def test_skill_calls_tools_by_the_name_the_plugin_registers():
    hooks = load_hooks()
    _, body = frontmatter()
    prefixes = set(re.findall(r"\b(readytrader_[a-z_]+)\.[a-z_]+", body))
    assert prefixes == {hooks.MCP_NAME} == {manifest()["name"]}
    assert "mcp_server_name" not in hooks.DEFAULTS  # the name is not a setting


def _examples(body):
    """(tool, arguments) for every `readytrader_stocks.<tool>` with `{...}` in the skill, and the JSON example."""
    found = []
    for tool, args in re.findall(r"`readytrader_stocks\.([a-z_]+)` with\s+`(\{.*?\})`", body, re.S):
        found.append((tool, json.loads(re.sub(r"<[^>]*>", "0", args))))
    for block in re.findall(r"```json\n(.*?)```", body, re.S):
        call = json.loads(block)
        found.append((call["tool_name"].split(".", 1)[1], call["tool_args"]))
    return found


def test_skill_examples_use_only_the_tools_real_arguments():
    _, body = frontmatter()
    examples = _examples(body)
    assert {"get_stock_price", "deposit_paper_funds", "validate_trade_risk", "place_market_order"} <= {t for t, _ in examples}
    for tool, args in examples:
        assert set(args) <= set(SERVER_TOOLS[tool]), f"{tool}: {sorted(set(args) - set(SERVER_TOOLS[tool]))}"


# ------------------------------------------------------------------ product constants, pinned to the server

def test_paper_profile_is_exactly_the_servers_switches():
    assert load_hooks().PAPER_ENV == EXPECTED_PAPER_ENV


def test_every_data_file_the_server_writes_is_pinned():
    assert load_hooks().DB_FILES == EXPECTED_DB_FILES


def test_required_tools_are_the_skills_procedure_and_exist_on_the_server():
    hooks = load_hooks()
    assert set(hooks.REQUIRED_TOOLS) <= set(SERVER_TOOLS)
    assert {"get_stock_price", "deposit_paper_funds", "validate_trade_risk", "place_market_order"} <= set(hooks.REQUIRED_TOOLS)
    assert {tool for tool, _, _ in hooks.PAPER_GATE} <= set(hooks.REQUIRED_TOOLS)


def test_paper_gate_is_the_servers_paper_mode_refusal():
    hooks = load_hooks()
    assert hooks.PAPER_GATE == EXPECTED_GATE
    for tool, args, _ in hooks.PAPER_GATE:
        assert set(args) <= set(SERVER_TOOLS[tool])


def test_entrypoint_and_names():
    hooks = load_hooks()
    assert hooks.ENTRYPOINT == "app/main.py"
    assert hooks.PLUGIN_NAME == hooks.MCP_NAME == manifest()["name"] == "readytrader_stocks"


def test_skill_never_sends_a_price_on_market_orders():
    _, body = frontmatter()
    order = re.search(r"place_market_order` with `(\{.*?\})`", body, re.S)
    assert order and '"price"' not in order.group(1)
