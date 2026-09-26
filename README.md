# a0-readytrader-stocks-plugin

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Agent Zero Plugin](https://img.shields.io/badge/Agent%20Zero-Plugin-blue)](https://github.com/agent0ai/agent-zero)

**Paper stock trading for [Agent Zero](https://github.com/agent0ai/agent-zero)** through the
[ReadyTrader-Stocks](https://github.com/up2itnow0822/ReadyTrader-Stocks) MCP server.

Installing the plugin installs the ReadyTrader-Stocks server into the plugin's own folder, registers
it with Agent Zero's MCP client, and adds a paper-trading skill. Your agent can then read US stock
quotes, candles, market regime and news, run backtests and risk checks, and place **paper** orders
against a simulated wallet. The server always runs the paper profile — paper mode, trading halted,
live execution disabled, every broker client that has a paper/sandbox switch set to it, no broker
keys — and the plugin has no switch that changes that.

Requires Agent Zero v2.13 or later (plugin system with lifecycle hooks), Git in the Agent Zero
environment, network access to GitHub and PyPI during installation, and a ReadyTrader-Stocks version
from its public-release update (PR #4, September 2026, or later). Earlier releases cannot start the
way Agent Zero launches MCP servers, and installation refuses them.

## Install

Open **Plugins** in Agent Zero and click **Install** (the Plugin Hub). Either tab works; each runs
the plugin's `install()` hook:

- **Git:** enter `https://github.com/up2itnow0822/a0-readytrader-stocks-plugin`.
- **ZIP:** download this repository as a ZIP and upload it.

Installation takes a few minutes. `install()`:

1. clones ReadyTrader-Stocks into `usr/plugins/readytrader_stocks/server` (branch `main` by default)
   and installs its requirements into `server/.venv` with Agent Zero's own Python (3.12+);
2. starts the server once over MCP stdio, checks that it offers the tools the skill uses, and checks
   that it answers a brokerage-account call (`start_brokerage_private_ws`) with
   `paper_mode_not_supported`, i.e. that it runs in paper mode (this proves paper mode, not which
   server version it is);
3. adds an entry named `readytrader_stocks` under **Settings &rarr; MCP/A2A &rarr; External MCP
   Servers**, pointing at that server with the paper profile. The name is fixed: the skill calls the
   tools as `readytrader_stocks.<tool>`. Your other servers are kept as they are
   (the settings JSON is saved back as `{"mcpServers": {...}}`, so comments and formatting in it are
   not kept), and nothing is written if any earlier step fails.

The server checkout gets an empty `.env` so ReadyTrader-Stocks does not read Agent Zero's own
`usr/.env` (your model API keys and UI password).

Copying the folder into `usr/plugins/` by hand does not run `install()`; use the Git URL or ZIP
installer. (If you did copy it, open the plugin's settings and click **Save**: that runs the same
setup.)

**Upgrading from 1.0.0:** uninstall 1.0.0 first, then install this version. 1.0.0 has no install
hook, so an in-place update would not set up the server.

## Use

Open a new chat and ask, for example, *"What is AAPL trading at?"* or *"Paper-buy 1 share of AAPL
after a risk check."* The agent sees the server's 20 tools as `readytrader_stocks.<tool>` and the
`readytrader-stocks-paper` skill, which gives it the procedure: take the price from
`get_stock_price`, run `validate_trade_risk` and continue only when it allows the trade, then place
a market order without a price (the server fills it at its market price and repeats the risk check
against the wallet). Each order may be at most 5% of the portfolio; the limit applies to each order,
not to the position, so several orders can build a larger one. The paper wallet and order history are kept in
`usr/plugins/readytrader_stocks/data`.

Paper orders fill at once: the entry sets ReadyTrader-Stocks' approval mode to `auto`, because in
`approve_each` mode the server holds even paper orders for an operator approval that no agent can
give. Approvals exist for live orders, and this plugin never enables live trading.

Asking for a live trade gets a refusal. Live trading is an operator process run on ReadyTrader-Stocks
itself, with its own broker keys and approvals, never through this plugin.

## Settings

The plugin's settings screen (Settings &rarr; External, or the plugin's entry under **Plugins**) holds
the server repository and version (branch, tag or full commit SHA) and the start-up and tool-call
timeouts. The paper profile and the MCP server name (`readytrader_stocks`) are not settings.

**Saving applies the settings at once:** the server is moved to the chosen version, checked, and the
MCP entry is registered again. A version change can take a few minutes. If any step fails, the
settings are not saved, a notification names the step, and the server goes back to the version it
was on (a server folder the setup had to create is moved aside as `server.failed-*` instead of being
left behind the MCP entry). Saving again without changes re-runs the setup, which also repairs it (for
example after an Agent Zero upgrade changed its Python), and makes Agent Zero reload the server's tools.
The MCP settings must be strict JSON for any of this: the plugin will not rewrite settings with
comments or trailing commas, because that could drop what you wrote.

To choose a setting before the first install (for example a server version), give Agent Zero the environment variable
`A0_SET_readytrader_stocks__<setting>`, e.g. `A0_SET_readytrader_stocks__server_ref=v1.2.3` (in
`usr/.env`, or `-e` for the Docker container), and restart Agent Zero. The install uses it while no
settings have been saved; once you save the settings screen, the saved values win. (The screen's
**Reset to default** shows the plugin's own defaults, not the preset.)

The server repository is code the plugin runs: the setup clones it, installs its requirements and
starts its `app/main.py` with Agent Zero's Python. Leave it at ReadyTrader-Stocks, or point it only at
a fork you trust.

## Update and uninstall

- **A newer plugin version:** installed from the Plugin Index, use **Update** on the plugin's Plugin
  Hub page; it re-runs `install()` and keeps the paper wallet. Installed from a Git URL or ZIP (Agent
  Zero offers no Update button for those), uninstall and install again; that resets the settings and
  the paper wallet.
- **A newer server version:** set it in the settings and save.
- **Uninstall** removes the `readytrader_stocks` MCP entry, then Agent Zero deletes the plugin folder,
  including the server and the paper wallet. If the MCP settings cannot be read, the uninstall still
  goes ahead and the log says which entry to remove by hand.

## Troubleshooting

- **Install or save fails:** the message names the step (cloning, fetching the version, installing
  requirements, starting the server, the paper-mode check). Check that Agent Zero can reach GitHub and
  PyPI, then try again.
- **A start-up error naming a missing module** (`No module named 'app'`, `pydantic_settings`): the
  server version predates ReadyTrader-Stocks' public-release update, whose releases cannot start the
  way Agent Zero launches MCP servers. Set a newer server version.
- **"the server did not show that it runs in paper mode"**: the server answered the paper-mode check
  with something other than `paper_mode_not_supported`, so the plugin did not register it. Set a
  ReadyTrader-Stocks version with PR #4 or later.
- **"An MCP server named ... already exists (Agent Zero calls it readytrader_stocks)":** you have your
  own entry with that name, or one Agent Zero treats as the same (it lowercases names and turns other
  characters into `_`). Remove or rename it, then install again (or save the plugin settings). The
  plugin cannot use another name: its skill calls the tools as `readytrader_stocks.<tool>`, and with
  another name those calls would reach your server instead.
- **No tools in the chat:** open Settings &rarr; MCP/A2A &rarr; External MCP Servers and check the
  entry's status and tool count. An Agent Profile with a custom tool policy can block MCP tools. Saving
  the plugin settings re-runs the setup.
- **Market data errors behind a proxy:** Agent Zero starts MCP servers with a minimal environment
  (no proxy variables). Add `HTTPS_PROXY` (and `NO_PROXY`, or a CA bundle variable if your proxy needs
  one) to the `readytrader_stocks` entry's `env` in the MCP settings. The setup keeps only these
  variables there (any letter case): `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, `ALL_PROXY`,
  `SSL_CERT_FILE`, `SSL_CERT_DIR`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`,
  `WEBSOCKET_CLIENT_CA_BUNDLE`, `TZ`, `LANG`, `LC_ALL`. Anything else you add (broker or provider
  keys, other ReadyTrader settings) is removed the next time the setup runs, and the paper-profile
  and data-path variables are always reset.
- **Projects:** a project whose own MCP settings define a server Agent Zero also calls
  `readytrader_stocks` uses that server instead of this one inside that project.
- **Slow first call:** Agent Zero starts the server afresh for every call; allow a few seconds.

## Development

```bash
python -m pip install -r requirements-dev.txt
pytest -q
```

`hooks.py` holds the lifecycle hooks, `skills/readytrader-stocks-paper/SKILL.md` the agent-facing
procedure, `webui/config.html` the settings screen. The 1.x HTTP tool client is kept in `_deprecated/`
for reference; Agent Zero never loads it.

## Ecosystem

- [ReadyTrader-Stocks](https://github.com/up2itnow0822/ReadyTrader-Stocks) — the MCP server
- [a0-readytrader-crypto-plugin](https://github.com/up2itnow0822/a0-readytrader-crypto-plugin) and
  [a0-readytrader-forex-plugin](https://github.com/up2itnow0822/a0-readytrader-forex-plugin) — the same design for crypto and forex
- [agent-wallet-sdk](https://github.com/up2itnow0822/agent-wallet-sdk) — Non-custodial agent wallets (`npm install agentwallet-sdk`)
- [agentpay-mcp](https://github.com/up2itnow0822/agentpay-mcp) — MCP server for agent payments
- [webmcp-sdk](https://github.com/up2itnow0822/webmcp-sdk) — Browser-native WebMCP integration
- [AgentNexus2](https://github.com/up2itnow0822/AgentNexus2) — TaskBridge agent marketplace

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE)
