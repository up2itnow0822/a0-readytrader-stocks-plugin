# Changelog

## [2.0.0] - 2026-09-25

Rebuilt for the Agent Zero v2.13 plugin system. Version 1.0.0 could not work in current Agent Zero
(public-release UAT, run 2026-09-25-01): its tool file failed to import (`python.helpers` no longer
exists), Agent Zero loads one tool class per file by file name, no tool prompts told the model the
tools existed, the settings screen bound to a store Agent Zero does not have, and the tools posted
to an HTTP path (`/mcp/call_tool`) that ReadyTrader-Stocks does not serve (its MCP server speaks
stdio), with arguments the server rejects and one tool (`get_fundamental_data`) it does not have.

### Changed (breaking)
- The plugin now connects ReadyTrader-Stocks through Agent Zero's own MCP client. `hooks.py`
  `install()` clones the server into the plugin folder, installs its requirements with Agent Zero's
  Python, checks that it starts (in a separate process, with positive proof), and registers it under
  Settings -> MCP/A2A -> External MCP Servers as `readytrader_stocks` (a fixed name: the skill calls the
  tools by it); `uninstall()` removes that entry.
  The agent gets all 20 server tools with their real schemas instead of six hand-written wrappers.
- The server always runs the paper profile (paper mode, trading halted, live execution disabled,
  every broker client with a paper/sandbox switch on it, no keys); there is no live setting. Paper orders fill at
  once (approval mode `auto`: approvals are for live orders, which this plugin never enables). The
  paper wallet lives in the plugin folder.
- New skill `readytrader-stocks-paper`: the paper procedure (quote, risk verdict, market order
  without a price, refusal codes) and the rule to refuse live trading.
- Settings are now the server repository and version and the timeouts; the
  old keys (`mcp_server_url`, `trading_mode`, risk limits, key env names) did nothing or broke calls.
  Saving the settings screen applies them at once (`save_plugin_config` hook): on failure nothing is
  saved and the server checkout is rolled back. Saving again repairs the setup.
- Install and every save check that the server starts, offers the skill's tools and answers a
  brokerage-account call with `paper_mode_not_supported` (proof that it runs in paper mode), and refuse
  to register it otherwise. The plugin needs ReadyTrader-Stocks PR #4 or later: earlier releases cannot
  start the way Agent Zero launches MCP servers.
- The 1.x tools and tests moved to `_deprecated/` (Agent Zero does not load them).
- Upgrading from 1.0.0: uninstall it, then install 2.0.0 (1.0.0 has no install hook).

### Security
- The server checkout gets an empty `.env`, so ReadyTrader-Stocks never reads Agent Zero's `usr/.env`
  (model API keys, UI password) when it walks up for a `.env` file.
- The MCP entry keeps only proxy and CA-bundle variables a user adds (any other variable, including
  broker keys, is removed on every setup); the paper profile and every data path (both
  `<X>_DB_PATH` and `READYTRADER_<X>_DB_PATH`) are reset in any letter case.
- `server_ref` must be a branch, tag or full commit SHA; a value git could read as an option is refused.
- The MCP entry's name is fixed (`readytrader_stocks`), not a setting: the skill calls the tools by that
  name, so an entry under another name would leave its calls to whatever server holds this one. A
  server name Agent Zero would treat as the plugin's own is refused instead of shadowing the paper
  server. MCP settings are rewritten only when they are strict JSON, keeping every other server,
  top-level key and non-ASCII text. Setups are serialised with a file lock. Notifications are escaped.

### Fixed
- README: the install steps (the old `pip install -r requirements.txt` had no file to install) and the
  broker key variables nothing read.
- The URL guard of 1.x no longer matters: the plugin makes no HTTP calls.

## [1.0.0] - 2026-03-23

### Added
- Initial release
- Agent Zero plugin for automated stock trading via the ReadyTrader strategy engine
- Tools for market data, order execution, and portfolio management via ReadyTrader-Stocks MCP server
