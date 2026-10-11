# a0-readytrader-stocks-plugin

Root AGENTS.md (DOX rail).

## Purpose

Agent Zero community plugin `readytrader_stocks`: paper stock trading through the ReadyTrader-Stocks MCP
server. The repository root is the plugin folder Agent Zero installs into `usr/plugins/readytrader_stocks/`.

## Ownership

- Owner: Agent Economy, LLC / Bill Wilson (@up2itnow0822)
- Companion server: https://github.com/up2itnow0822/ReadyTrader-Stocks (source of truth for tools and env vars)
- Host contract: Agent Zero v2.13+ `plugins/AGENTS.md`, `helpers/plugins.py`, `helpers/mcp_handler.py`,
  `helpers/skills.py`, `skills/a0-create-plugin/references/implementation.md`

## Local Contracts

- `plugin.yaml` — Agent Zero manifest fields only; `name` == `readytrader_stocks`; `settings_sections` from
  Agent Zero's set (`agent`, `external`, `mcp`, `developer`, `backup`); `version` matches CHANGELOG and the
  skill
- `hooks.py` — the only lifecycle code: `install()` and `save_plugin_config()` both run `apply(cfg)` under a
  file lock (read + clash-check the MCP settings, clone/fetch the server at `server_ref` into `server/`, venv
  with Agent Zero's Python, requirements, an empty `server/.env` so the server never reads Agent Zero's
  `usr/.env`, the smoke test, then register the MCP entry last, or ask Agent Zero to reload it when the entry
  text is unchanged); any failure before registration completes rolls the checkout back (or moves an
  unverified fresh checkout aside as `server.failed-*`) and writes nothing. `save_plugin_config()` validates
  and applies before Agent Zero writes `config.json`, so saved settings are always applied settings.
  `pre_update()` is a no-op; `uninstall()` removes only our entry and never raises. Setup never lives in
  `execute.py`
- `mcp_smoke.py` — the smoke test, run by `hooks.smoke_test()` in its own Python process (never inside Agent
  Zero's patched event loop); the server is registered only on its positive proof (tool count, required
  tools present, gate passed); a timeout kills the process group
- `PAPER_GATE` — the server must answer `start_brokerage_private_ws` with `paper_mode_not_supported`: proof
  that it runs in paper mode, not of its revision. The plugin needs ReadyTrader-Stocks PR #4+; earlier
  releases cannot start as Agent Zero launches them (`app/main.py` from outside the repo) and are never
  registered. Docs must not claim the check identifies a revision
- The MCP entry always carries `PAPER_ENV` (paper, halted, live disabled, every broker client with a
  paper/sandbox switch on it: Alpaca, Tradier, E*TRADE; approval mode `auto` because the server holds even
  paper orders for operator approval otherwise) and every data path under `data/`, under both names the server
  reads (`<X>_DB_PATH`, `READYTRADER_<X>_DB_PATH`); no setting may change the paper profile; no credentials
  are ever written
- Registration never overwrites or removes a server the plugin did not add, including one whose name Agent
  Zero normalises to ours; it reads every `mcp_servers` shape Agent Zero accepts and writes back
  `{"mcpServers": {...}}` with every server and other top-level key kept; non-strict JSON is never
  rewritten. Our entry is recognised by `MARKER`, or by its `app/main.py` entrypoint only under `MCP_NAME`
  (another name pointing at our server is the user's). A refresh keeps `disabled` / `disabled_tools`
  and only the proxy/CA variables in `USER_ENV_ALLOWED`; everything else the user added is removed
- The MCP entry's name is fixed (`MCP_NAME` = `readytrader_stocks`), never a setting: the skill's tool calls
  are `readytrader_stocks.<tool>`, and under another name they would reach whatever server holds that name
- Settings are validated in `normalize_config`: https repo or absolute directory, `server_ref` a
  branch/tag/full SHA that can never be read as a git option, timeouts 5-3600 s
- `skills/readytrader-stocks-paper/SKILL.md` — Agent Zero skill rules (name `^[a-z0-9-]+$`, description
  <= 1024); names only real ReadyTrader-Stocks tools; market orders never carry a `price`
- `webui/config.html` binds only `config.<key>` for exactly the keys in `default_config.yaml`
- No `tools/` directory: the agent uses the server's tools through Agent Zero's MCP client. The 1.x tool
  client and its tests stay in `_deprecated/` (never loaded)
- `server/`, `data/`, `config.json` are created at install time and are never committed
- No UAT records in the tree: the repository root ships to every user's Agent Zero

## Work Guidance

- When ReadyTrader-Stocks adds or renames a tool or a tool parameter, update `SERVER_TOOLS` (tool -> parameters,
  from the server's MCP schemas) in `tests/test_contract.py` and the skill; the contract tests pin
  `PAPER_ENV`, `DB_FILES`, `REQUIRED_TOOLS`, `PAPER_GATE`, `ENTRYPOINT` and `MCP_NAME` against it
- Verify behaviour in a real Agent Zero framework process (hooks + MCP client), not only with the unit fakes
- Paper-first only; never add a live-trading path or a setting that could enable one

## Verification

- `pytest -q` (hooks with framework fakes, real git checkouts and venvs, the smoke test and paper gate
  against `tests/fake_server.py` over real MCP stdio, manifest / settings-screen / skill contract);
  needs `requirements-dev.txt` (pytest, PyYAML, mcp, nest_asyncio for the "inside Agent Zero's loop" test)
- Agent Zero integration: install into a throwaway Agent Zero v2.13 tree through its installer (Git URL and
  ZIP), initialise `MCPConfig` from the saved settings, call the paper procedure's tools, save settings
  through the real settings screen, update, uninstall

## Child DOX Index

(none)
