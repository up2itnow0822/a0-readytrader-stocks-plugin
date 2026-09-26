"""A stand-in MCP stdio server for smoke_test(). FAKE_TOOLS (comma list) are offered; FAKE_GATE is the real server's
paper-mode refusal as JSON [tool, args, code] (the tests pass test_contract's pinned values, never hooks.py's, so a
wrong constant in hooks.py is caught); FAKE_MODE picks how the server behaves:

current     offers every tool and answers the gate tool with the expected refusal (a current server)
old         same tools, but the gate tool answers ok (a revision without the paper-mode refusal)
missing     offers only the first tool
crash       writes an error to stderr and exits before speaking MCP
crashbytes  the same, with bytes that are not UTF-8
hang        never answers
"""

import json
import os
import sys
import time

mode = os.environ.get("FAKE_MODE", "current")
if mode == "crash":
    print("Traceback: ModuleNotFoundError: No module named 'yfinance'", file=sys.stderr, flush=True)
    sys.exit(1)
if mode == "crashbytes":
    sys.stderr.buffer.write(b"fatal: \xff\xfe not utf-8, then ModuleNotFoundError: No module named 'yfinance'\n")
    sys.stderr.flush()
    sys.exit(1)
if mode == "hang":
    time.sleep(3600)

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("fake-readytrader")
tools = [t for t in os.environ["FAKE_TOOLS"].split(",") if t]
gate_tool, gate_args, gate_code = json.loads(os.environ["FAKE_GATE"])
if mode == "missing":
    tools = tools[:1]


def _ok() -> str:
    return json.dumps({"ok": True, "data": {}})


for name in tools:
    if name == gate_tool and mode != "missing":
        continue
    mcp.tool(name=name)(_ok)

if mode != "missing":
    params = ", ".join(f"{k}: str = ''" for k in gate_args)
    body = ("json.dumps({'ok': True, 'data': {}})" if mode == "old" else
            f"json.dumps({{'ok': False, 'error': {{'code': {gate_code!r}, 'message': 'paper', 'data': {{}}}}}})")
    namespace = {"json": json}
    exec(f"def gate({params}) -> str:\n    return {body}\n", namespace)
    mcp.tool(name=gate_tool)(namespace["gate"])

mcp.run()
