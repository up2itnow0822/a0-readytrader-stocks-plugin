# a0-readytrader-stocks-plugin

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Agent Zero Plugin](https://img.shields.io/badge/Agent%20Zero-Plugin-blue)](https://github.com/frdel/agent-zero)

**Agent Zero plugin** for automated Stocks trading via the ReadyTrader strategy engine.

## Installation

```bash
git clone https://github.com/up2itnow0822/a0-readytrader-stocks-plugin.git
cd a0-readytrader-stocks-plugin
# Install into Agent Zero using the plugin name expected by the index
cp -r . /path/to/agent-zero/usr/plugins/readytrader_stocks
pip install -r requirements.txt
```

## Usage

```python
# Add to Agent Zero plugins directory and configure your broker credentials
```

## Configuration

Set the following environment variables:
```bash
BROKER_API_KEY=your_key
BROKER_API_SECRET=your_secret
```

## Ecosystem

- [agent-wallet-sdk](https://github.com/up2itnow0822/agent-wallet-sdk) — Non-custodial agent wallets (`npm install agentwallet-sdk`)
- [agentpay-mcp](https://github.com/up2itnow0822/agentpay-mcp) — MCP server for agent payments
- [webmcp-sdk](https://github.com/up2itnow0822/webmcp-sdk) — Browser-native WebMCP integration
- [AgentNexus2](https://github.com/up2itnow0822/AgentNexus2) — TaskBridge agent marketplace

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE)
