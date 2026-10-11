"""MCP stdio smoke test for the ReadyTrader server, run by hooks.smoke_test() in its own Python process.

A separate process keeps the check away from Agent Zero's patched event loop (nest_asyncio), where a timeout's
cancellation can be absorbed and a half-finished check would look like a pass. Reads one JSON object on stdin:

  {"command": str, "args": [str], "env": {str: str}, "timeout": seconds,
   "required_tools": [str], "gate": [[tool, args, expected_error_code], ...]}

and prints one JSON line: {"ok": true, "tools": <count>, "gate": "passed"} only after every check passed, else
{"ok": false, "error": "<what failed>", "stderr": "<the server's own output, tail>"}.
"""

import asyncio
import json
import sys
import tempfile


def answer(result) -> dict:
    text = " ".join(getattr(c, "text", "") for c in (result.content or []))
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    return data if isinstance(data, dict) else {"_raw": text[:300]}


async def check(spec: dict, errlog) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=spec["command"], args=spec["args"], env=spec["env"])
    async with stdio_client(params, errlog=errlog) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            missing = sorted(set(spec["required_tools"]) - names)
            if missing:
                return {"ok": False, "kind": "missing_tools", "error": f"the server does not offer {', '.join(missing)}"}
            for tool, args, code in spec["gate"]:
                got = answer(await session.call_tool(tool, args))
                if got.get("ok") is not False or (got.get("error") or {}).get("code") != code:
                    return {"ok": False, "kind": "gate", "tool": tool, "expected": code, "got": json.dumps(got)[:200]}
            return {"ok": True, "tools": len(names), "gate": "passed"}


def main() -> None:
    spec = json.loads(sys.stdin.read())
    with tempfile.TemporaryFile("w+b") as raw:
        errlog = open(raw.fileno(), "w", encoding="utf-8", errors="replace", closefd=False)
        try:
            result = asyncio.run(asyncio.wait_for(check(spec, errlog), timeout=spec["timeout"]))
        except (TimeoutError, asyncio.TimeoutError):
            result = {"ok": False, "kind": "timeout", "error": f"no answer within {spec['timeout']}s"}
        except BaseException as e:  # noqa: BLE001 - report every failure as data
            leaves, todo = [], [e]
            while todo:
                x = todo.pop(0)
                subs = getattr(x, "exceptions", None)
                todo.extend(subs) if subs else leaves.append(f"{type(x).__name__}: {x}".rstrip(": "))
            result = {"ok": False, "kind": "error", "error": "; ".join(leaves)}
        errlog.flush()
        raw.seek(0)
        result["stderr"] = raw.read().decode("utf-8", errors="replace").strip()[-1200:]
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
