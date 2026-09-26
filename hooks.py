"""Agent Zero lifecycle hooks for the ReadyTrader Stocks plugin (paper trading only).

Agent Zero calls these from its framework process (helpers.plugins.call_plugin_hook):

install()             after a Plugin Index / Git / ZIP install and after every update: set up the server
                      (below) with the saved settings.
save_plugin_config()  when the plugin's settings screen is saved: validate the new settings and set up
                      the server with them before Agent Zero writes them. If that fails, nothing is saved
                      and the server goes back to the version it was on. Saving again re-runs the setup,
                      which also repairs it, and makes Agent Zero reload the server's tools.
pre_update()          nothing to stop: Agent Zero starts the MCP server afresh for every call.
uninstall()           removes the MCP entry this plugin added (never blocks the uninstall). The server
                      and the paper account live in the plugin folder, which Agent Zero deletes next.

Setting up the server (apply): check that the MCP settings can be updated, clone ReadyTrader-Stocks into
<plugin>/server at `server_ref`, install its requirements into <plugin>/server/.venv with Agent Zero's own
Python, start it once over MCP stdio in a separate process (mcp_smoke.py) and require positive proof that it
lists the tools the skill uses and runs in paper mode, then register it under Settings -> MCP/A2A ->
External MCP Servers as `readytrader_stocks`. Any failure before registration completes rolls the checkout
back (or moves an unverified fresh checkout aside) and leaves the MCP settings as they were.

The server always runs the paper profile below. It is not a setting: this plugin never enables live
execution, and the entry it writes carries no credentials. The MCP server name is not a setting either: the
skill calls the tools as readytrader_stocks.<tool>, so an entry under any other name would leave those calls
to whatever server holds that name.
"""

from __future__ import annotations

import contextlib
import hashlib
import html
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

# ---------------------------------------------------------------------------------------------- product

PLUGIN_NAME = "readytrader_stocks"
MCP_NAME = PLUGIN_NAME  # the MCP entry's name, fixed: the skill's tool calls are readytrader_stocks.<tool>
TITLE = "ReadyTrader Stocks"
PRODUCT = "ReadyTrader-Stocks"
PLUGIN_DIR = Path(__file__).resolve().parent
SERVER_DIR = PLUGIN_DIR / "server"
DATA_DIR = PLUGIN_DIR / "data"
SMOKE_CLIENT = PLUGIN_DIR / "mcp_smoke.py"
ENTRYPOINT = "app/main.py"
MARKER = "added by the readytrader_stocks Agent Zero plugin; removed when it is uninstalled"
DESCRIPTION = f"{PRODUCT} paper trading (PAPER_MODE, halted, live disabled) - {MARKER}"
MIN_PYTHON = (3, 12)

DEFAULTS = {
    "server_repo": "https://github.com/up2itnow0822/ReadyTrader-Stocks.git",
    "server_ref": "main",
    "init_timeout": 60,
    "tool_timeout": 120,
}

# The paper profile. Fixed on purpose. Paper, halted and live-disabled close the live path three times
# over; as a last backstop, every broker client that has a paper/sandbox switch (Alpaca, Tradier, E*TRADE)
# is set to it. Schwab and Robinhood have none; no keys ever reach the server, so no client can connect.
# EXECUTION_APPROVAL_MODE is "auto" because ReadyTrader-Stocks holds even paper orders for an operator
# approval in approve_each mode, and there is no approval step an agent could (or should) perform here.
PAPER_ENV = {
    "PAPER_MODE": "true",
    "LIVE_TRADING_ENABLED": "false",
    "TRADING_HALTED": "true",
    "EXECUTION_APPROVAL_MODE": "auto",
    "MARKET_GUARD_ENABLED": "true",
    "ALPACA_PAPER": "true",
    "TRADIER_SANDBOX": "true",
    "ETRADE_SANDBOX": "true",
}
# Every data file the server writes, pinned to <plugin>/data under both names the server reads
# (READYTRADER_<X>_DB_PATH first, then <X>_DB_PATH): Agent Zero starts MCP servers without a working
# directory of their own, and nothing a user adds to the entry may move the paper account elsewhere.
DB_FILES = ("PAPER", "AUDIT", "EXECUTION", "IDEMPOTENCY", "INSIGHT", "STRATEGY")

# Tools the skill relies on; a server without them is the wrong repository or revision.
REQUIRED_TOOLS = ("get_stock_price", "deposit_paper_funds", "validate_trade_risk", "place_market_order",
                  "start_brokerage_private_ws")
# The paper-safety gate: the running server must show that it is in paper mode, i.e. that the paper profile
# reached it, by answering a brokerage-account call with paper_mode_not_supported. It proves paper mode, not
# the revision. The plugin needs ReadyTrader-Stocks with PR #4: earlier releases cannot start the way Agent
# Zero launches MCP servers (so the smoke test refuses them); a commit between those is the user's choice.
PAPER_GATE = [("start_brokerage_private_ws", {"brokerage": "alpaca"}, "paper_mode_not_supported")]
PAPER_GATE_PR = "PR #4, September 2026"

# The only variables a user may add to our entry (any letter case): network plumbing the paper profile
# needs behind a proxy. Anything else is removed on the next setup, so no credential, broker setting or
# setting outside the paper profile can reach the server through this plugin.
USER_ENV_ALLOWED = {"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR",
                    "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "WEBSOCKET_CLIENT_CA_BUNDLE", "TZ", "LANG", "LC_ALL"}

SMOKE_MIN_SECONDS = 30  # the smoke test waits at least this long for the first start (cold imports)

_THREAD_LOCK = threading.Lock()


class SetupError(RuntimeError):
    """A setup step failed; the message names the step."""


# ---------------------------------------------------------------------------------------------- config

def check_repo(url) -> str:
    """Only an https Git URL or an existing local directory (a clone you manage) is accepted. The
    repository is code the plugin runs, so the host is the user's choice, like any plugin source."""
    url = str(url).strip()
    if url.startswith("https://") and len(url) > len("https://") and not any(c.isspace() for c in url):
        return url
    if os.path.isabs(url) and os.path.isdir(url):
        return url
    raise SetupError(f"server_repo must be an https:// Git URL or an existing absolute directory, not {url!r}")


def check_ref(ref) -> str:
    """A branch, tag or full commit SHA; never something git could read as an option."""
    ref = str(ref).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", ref) or ".." in ref or ref.endswith((".lock", "/")):
        raise SetupError(f"server_ref must be a branch, tag or full commit SHA, not {ref!r}")
    return ref


def _timeout(key: str, value) -> int:
    if isinstance(value, bool):
        raise SetupError(f"{key} must be a whole number of seconds, not {value!r}")
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        raise SetupError(f"{key} must be a whole number of seconds, not {value!r}") from None
    if not 5 <= seconds <= 3600:
        raise SetupError(f"{key} must be between 5 and 3600 seconds, not {seconds}")
    return seconds


def normalize_config(saved: dict | None) -> dict:
    """Defaults overlaid with saved values; every value validated."""
    cfg = dict(DEFAULTS)
    for key in DEFAULTS:
        value = (saved or {}).get(key)
        if value not in (None, ""):
            cfg[key] = value
    cfg["server_repo"] = check_repo(cfg["server_repo"])
    cfg["server_ref"] = check_ref(cfg["server_ref"])
    cfg["init_timeout"] = _timeout("init_timeout", cfg["init_timeout"])
    cfg["tool_timeout"] = _timeout("tool_timeout", cfg["tool_timeout"])
    return cfg


def load_config() -> dict:
    """The saved settings (config.json, via Agent Zero) or the defaults. A config.json Agent Zero
    cannot read is an error, not a silent fall-back to the defaults."""
    try:
        from helpers.plugins import get_plugin_config  # Agent Zero framework (faked in unit tests)
    except ImportError:
        return normalize_config({})
    try:
        saved = get_plugin_config(PLUGIN_NAME) or {}
    except Exception as e:
        raise SetupError(f"reading the saved plugin settings (config.json) failed: {e}") from e
    if not isinstance(saved, dict):
        raise SetupError("the saved plugin settings (config.json) are not a JSON object")
    return normalize_config(saved)


# ---------------------------------------------------------------------------------------------- server

def _run(cmd: list, step: str, timeout: int = 1800, cwd: Path | None = None) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}  # a bad URL must fail, not wait for a password
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise SetupError(f"{step}: no result after {timeout}s") from None
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-1500:]
        raise SetupError(f"{step} failed: {tail or f'exit {proc.returncode}'}")
    return proc.stdout


def _git(*args: str, step: str) -> str:
    return _run(["git", "-C", str(SERVER_DIR), *args], step)


def venv_python() -> Path:
    return SERVER_DIR / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _python_works(py: Path) -> bool:
    try:
        return py.exists() and subprocess.run([str(py), "-c", "import sys"], capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def ensure_venv() -> Path:
    """The server's own venv, built with Agent Zero's Python; rebuilt (with --clear, which also drops the
    requirements stamp) when its interpreter is missing or broken, e.g. after an Agent Zero upgrade."""
    py = venv_python()
    if _python_works(py):
        return py
    if sys.version_info < MIN_PYTHON:
        raise SetupError(f"{PRODUCT} needs Python {'.'.join(map(str, MIN_PYTHON))}+; Agent Zero runs {sys.version.split()[0]}")
    _run([sys.executable, "-m", "venv", "--clear", str(SERVER_DIR / ".venv")], "creating the server's virtual environment")
    return py


def _stamp() -> Path:
    return SERVER_DIR / ".venv" / ".readytrader-requirements.sha256"


def ensure_requirements(py: Path) -> None:
    reqs = SERVER_DIR / "requirements.txt"
    if not reqs.is_file():
        raise SetupError(f"the server checkout has no requirements.txt; is server_repo a {PRODUCT} repository?")
    digest = hashlib.sha256(reqs.read_bytes()).hexdigest()
    stamp = _stamp()
    if stamp.exists() and stamp.read_text().strip() == digest:
        return
    stamp.unlink(missing_ok=True)  # a failed install must be retried next time
    _run([str(py), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "-r", str(reqs)],
         f"installing {PRODUCT}'s requirements")
    stamp.write_text(digest)


def ensure_dotenv_stop() -> None:
    """ReadyTrader loads the first .env it finds walking up from its code, which inside Agent Zero is
    usr/.env (Agent Zero's own secrets). An empty .env in the checkout (git-ignored there) stops the walk."""
    stop = SERVER_DIR / ".env"
    if not stop.exists():
        stop.write_text(f"# Placed by the {PLUGIN_NAME} Agent Zero plugin. {PRODUCT} loads the first .env it finds\n"
                        "# above its code; this empty one keeps it from reading Agent Zero's usr/.env.\n")


def _move_aside(path: Path, label: str) -> Path:
    target = path.with_name(f"{path.name}.{label}-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}")
    path.rename(target)
    return target


def ensure_server(cfg: dict) -> tuple[Path, dict]:
    """Check out server_ref and prepare it; returns (venv python, undo state for restore_server)."""
    repo, ref = cfg["server_repo"], cfg["server_ref"]
    if SERVER_DIR.exists() and not (SERVER_DIR / ".git").exists():
        # Something else is in the way: keep it (never delete) and start clean beside it.
        _move_aside(SERVER_DIR, "moved")
    undo = {"head": None, "url": None, "fresh": False}
    if not SERVER_DIR.exists():
        _run(["git", "clone", "--quiet", "--no-checkout", repo, str(SERVER_DIR)], f"cloning {repo}")
        undo["fresh"] = True
    else:
        try:
            undo["head"] = _git("rev-parse", "--verify", "HEAD", step="reading the current server version").strip()
        except SetupError:
            undo["head"] = None
        undo["url"] = _git("remote", "get-url", "origin", step="reading the server's origin").strip()
        if undo["url"] != repo:
            _git("remote", "set-url", "origin", repo, step="pointing the server checkout at server_repo")
    try:
        _git("fetch", "--quiet", "origin", ref, step=f"fetching {ref!r} from {repo}")
        _git("checkout", "--quiet", "--detach", "FETCH_HEAD", step=f"checking out {ref!r}")
        if not (SERVER_DIR / ENTRYPOINT).is_file():
            raise SetupError(f"{repo} at {ref!r} has no {ENTRYPOINT}; is server_repo a {PRODUCT} repository?")
        py = ensure_venv()
        ensure_requirements(py)
        ensure_dotenv_stop()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        restore_server(undo)
        raise
    return py, undo


def restore_server(undo: dict) -> None:
    """Undo a failed setup (best effort; never raises): put an existing checkout back where it was, or move
    a checkout this setup created (never verified) aside so no unverified code sits behind our MCP entry."""
    try:
        if undo.get("head"):
            if undo.get("url"):
                _git("remote", "set-url", "origin", undo["url"], step="restoring the server's origin")
            _git("checkout", "--quiet", "--detach", undo["head"], step="restoring the previous server version")
            py = venv_python()
            if _python_works(py):
                ensure_requirements(py)
        elif SERVER_DIR.exists():
            moved = _move_aside(SERVER_DIR, "failed")
            _log(f"the unverified server checkout was moved to {moved.name}")
    except Exception as e:  # the original error is the one to report
        _log(f"could not fully restore the previous server version: {e}")


def server_env() -> dict:
    env = dict(PAPER_ENV)
    env["READYTRADER_DATA_DIR"] = str(DATA_DIR)
    for name in DB_FILES:
        path = str(DATA_DIR / f"{name.lower()}.db")
        env[f"{name}_DB_PATH"] = path
        env[f"READYTRADER_{name}_DB_PATH"] = path
    return env


def mcp_entry(cfg: dict, python: Path) -> dict:
    return {
        "description": DESCRIPTION,
        "type": "stdio",
        "command": str(python),
        "args": [str(SERVER_DIR / ENTRYPOINT)],
        "env": server_env(),
        "init_timeout": cfg["init_timeout"],
        "tool_timeout": cfg["tool_timeout"],
    }


def _base_env() -> dict:
    keep = ("HOME", "LOGNAME", "PATH", "SHELL", "TERM", "USER")  # what Agent Zero passes to stdio servers
    return {k: os.environ[k] for k in keep if k in os.environ}


def smoke_test(entry: dict) -> int:
    """Start the server exactly as Agent Zero will (same command, args and environment) in a separate
    process (mcp_smoke.py) and return its tool count only on positive proof that the required tools are
    offered and the paper gate passed. Any other outcome, including a timeout, raises."""
    limit = max(SMOKE_MIN_SECONDS, entry["init_timeout"])
    spec = {"command": entry["command"], "args": entry["args"], "env": {**_base_env(), **entry["env"]},
            "timeout": limit, "required_tools": list(REQUIRED_TOOLS), "gate": PAPER_GATE}
    proc = subprocess.Popen([sys.executable, str(SMOKE_CLIENT)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, start_new_session=os.name != "nt")
    try:
        out, err = proc.communicate(json.dumps(spec).encode(), timeout=limit + 30)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        raise SetupError(f"starting the server over MCP stdio failed: no answer within {limit}s") from None
    finally:
        _kill_tree(proc)  # the server and anything it started, whatever happened
    lines = out.decode("utf-8", errors="replace").strip().splitlines()
    try:
        result = json.loads(lines[-1]) if lines else {}
    except ValueError:
        result = {}
    tail = f"\nserver output:\n{result['stderr']}" if result.get("stderr") else ""
    if result.get("ok") is True and result.get("gate") == "passed" and isinstance(result.get("tools"), int) and result["tools"] > 0:
        return result["tools"]
    kind = result.get("kind")
    if kind == "gate":
        raise SetupError(
            f"the server did not show that it runs in paper mode: {result.get('tool')} answered {result.get('got')} "
            f"instead of {result.get('expected')!r}. The plugin registers only a server that does; set Server "
            f"version to {PRODUCT} with {PAPER_GATE_PR}, or later")
    if kind == "missing_tools":
        raise SetupError(f"{result.get('error')}; is server_repo a {PRODUCT} repository?")
    what = result.get("error") or (err.decode("utf-8", errors="replace").strip()[-600:] or f"the check exited {proc.returncode}")
    raise SetupError(f"starting the server over MCP stdio failed: {what}{tail}")


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is None or os.name != "nt":
        with contextlib.suppress(Exception):
            if os.name == "nt":
                proc.kill()
            else:
                os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(Exception):
        proc.wait(timeout=10)


# ---------------------------------------------------------------------------------------------- MCP settings

def a0_name(name: str) -> str:
    """The name Agent Zero gives a server (helpers.mcp_handler.normalize_name)."""
    return re.sub(r"[^\w]", "_", str(name).strip().lower(), flags=re.UNICODE)


def parse_servers(raw) -> tuple[dict, dict]:
    """Agent Zero's mcp_servers setting as ({name: config}, other top-level keys), for every shape Agent Zero
    accepts ({"mcpServers": {...}}, {"mcpServers": [...]}, [...], or a single server object). Raises on
    anything that cannot be rewritten without losing what the user wrote (including non-strict JSON, which
    Agent Zero reads leniently but a rewrite would silently change)."""
    text = raw if isinstance(raw, str) else json.dumps(raw)
    if not text.strip():
        return {}, {}
    try:
        data = json.loads(text)
    except ValueError:
        raise SetupError("the MCP settings are not strict JSON (comments, trailing commas or similar); "
                         "rewriting them could drop what you wrote") from None
    extras: dict = {}
    if isinstance(data, dict) and "mcpServers" in data:
        extras = {k: v for k, v in data.items() if k != "mcpServers"}
        data = data["mcpServers"]
        if isinstance(data, dict):
            if not all(isinstance(v, dict) for v in data.values()):
                raise SetupError("an entry under mcpServers is not an object")
            return {k: dict(v) for k, v in data.items()}, extras
    elif isinstance(data, dict):
        data = [data]  # a single server
    if isinstance(data, list):
        servers: dict = {}
        for item in data:
            if not isinstance(item, dict) or not str(item.get("name") or "").strip():
                raise SetupError("an MCP server in the settings has no name")
            name = str(item["name"]).strip()
            if name in servers:
                raise SetupError(f"two MCP servers in the settings are named {name!r}")
            servers[name] = {k: v for k, v in item.items() if k != "name"}
        return servers, extras
    raise SetupError("the MCP settings are not JSON this plugin can read")


def _read_servers() -> tuple[dict, dict]:
    from helpers import settings  # Agent Zero framework

    try:
        return parse_servers(settings.get_settings().get("mcp_servers") or "")
    except SetupError as e:
        raise SetupError(f"Settings -> MCP/A2A -> External MCP Servers: {e}; fix it there, then save the plugin settings again") from None


def _write_servers(servers: dict, extras: dict) -> None:
    from helpers import settings

    settings.set_settings_delta({"mcp_servers": json.dumps({"mcpServers": servers, **extras}, indent=4, ensure_ascii=False)})


def ours(entry, key: str | None = None, name: str | None = None) -> bool:
    """Our entry: it carries MARKER, or it is the entry under our name and still runs our entrypoint (the
    user edited its description). Another name pointing at our entrypoint is the user's."""
    if not isinstance(entry, dict):
        return False
    if MARKER in str(entry.get("description", "")):
        return True
    args = entry.get("args") or []
    return key is not None and key == name and bool(args) and str(args[0]) == str(SERVER_DIR / ENTRYPOINT)


def check_clash(name: str, servers: dict) -> None:
    clash = [k for k, v in servers.items() if a0_name(k) == name and not ours(v, k, name)]
    if clash:
        raise SetupError(
            f"An MCP server named {clash[0]!r} already exists (Agent Zero calls it {name!r}) and was not added by "
            f"this plugin. The plugin's skill calls the tools as {name}.<tool>, so it needs that name: remove or "
            "rename that server, then install again (or save the plugin settings)")


def keep_user_additions(entry: dict, previous) -> tuple[dict, list[str]]:
    """A refresh keeps what the user added to our entry in the MCP settings: proxy and CA-bundle variables
    (USER_ENV_ALLOWED; Agent Zero does not pass them to MCP servers on its own) and the disabled /
    disabled_tools switches. Every other variable is removed, so the paper profile, the data paths and the
    no-credentials rule always hold. Returns (entry, names of removed variables)."""
    if not isinstance(previous, dict):
        return entry, []
    merged = dict(entry)
    managed = {k.upper() for k in entry["env"]}
    extra, dropped = {}, []
    for key, value in (previous.get("env") or {}).items():
        if key.upper() in managed:
            continue
        if key.upper() not in USER_ENV_ALLOWED:
            dropped.append(key)
            continue
        extra[key] = value
    merged["env"] = {**extra, **entry["env"]}
    for key in ("disabled", "disabled_tools"):
        if key in previous:
            merged[key] = previous[key]
    return merged, dropped


def register(cfg: dict, entry: dict) -> bool:
    """Add or refresh this plugin's MCP entry; never touch another server. Returns True if written."""
    servers, extras = _read_servers()
    name = MCP_NAME
    check_clash(name, servers)
    previous = servers.get(name) if ours(servers.get(name), name, name) else next(
        (v for k, v in servers.items() if ours(v)), None)
    entry, dropped = keep_user_additions(entry, previous)
    if dropped:
        _notify("warning", f"Removed {', '.join(dropped)} from the {name} MCP entry: the plugin keeps only proxy and "
                           f"CA-bundle variables there, and runs {PRODUCT} without credentials.")
    mine = [k for k, v in servers.items() if k != name and ours(v)]  # an entry of ours under an older name goes
    if servers.get(name) == entry and not mine:
        return False
    servers = {k: v for k, v in servers.items() if k not in mine}
    servers[name] = entry
    _write_servers(servers, extras)
    return True


def unregister() -> bool:
    servers, extras = _read_servers()
    mine = [k for k, v in servers.items() if ours(v, k, MCP_NAME)]
    if mine:
        _write_servers({k: v for k, v in servers.items() if k not in mine}, extras)
    return bool(mine)


def reload_agent_zero_mcp() -> None:
    """Make Agent Zero reconnect to its MCP servers now, as its own settings change does, so a repaired or
    re-versioned server's tools are picked up even when the entry text did not change."""
    try:
        from helpers import defer, settings
        from helpers.mcp_handler import MCPConfig
    except ImportError:
        return  # not inside Agent Zero (unit tests)
    text = settings.get_settings().get("mcp_servers") or ""

    async def _update():
        MCPConfig.update(text)

    defer.DeferredTask().start_task(_update)


# ---------------------------------------------------------------------------------------------- setup

def _log(message: str) -> None:
    try:
        from helpers.print_style import PrintStyle

        PrintStyle.warning(f"[{PLUGIN_NAME}] {message}")
    except Exception:
        print(f"[{PLUGIN_NAME}] {message}", file=sys.stderr)


def _notify(kind: str, message: str) -> None:
    """An Agent Zero notification. Agent Zero renders the message as HTML, so it is escaped here."""
    try:
        from helpers.notification import NotificationManager, NotificationPriority, NotificationType

        NotificationManager.send_notification(NotificationType(kind), NotificationPriority.NORMAL,
                                              html.escape(message).replace("\n", "<br>"), title=TITLE,
                                              display_time=8 if kind != "error" else 30, group=PLUGIN_NAME)
    except Exception:
        _log(message)


@contextlib.contextmanager
def _setup_lock():
    """One setup at a time, across threads and across the module copies Agent Zero imports (it re-imports
    hooks.py whenever its hooks cache is cleared): a file lock next to the server folder."""
    with _THREAD_LOCK:
        SERVER_DIR.parent.mkdir(parents=True, exist_ok=True)
        with open(SERVER_DIR.parent / ".readytrader-setup.lock", "a+") as fh:
            try:
                import fcntl

                fcntl.flock(fh, fcntl.LOCK_EX)
            except ImportError:  # Windows
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                with contextlib.suppress(Exception):
                    if os.name == "nt":
                        import msvcrt

                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(fh, fcntl.LOCK_UN)


def apply(cfg: dict) -> int:
    """Set up the server with cfg and register it; on any failure roll back and raise."""
    with _setup_lock():
        servers, _ = _read_servers()  # before anything moves: the settings must be updatable
        check_clash(MCP_NAME, servers)
        python, undo = ensure_server(cfg)
        entry = mcp_entry(cfg, python)
        try:
            tools = smoke_test(entry)
            if not isinstance(tools, int) or isinstance(tools, bool) or tools < 1:
                raise SetupError(f"the smoke test gave no proof that the server works ({tools!r})")
            written = register(cfg, entry)
        except Exception:
            restore_server(undo)
            raise
        if not written:
            reload_agent_zero_mcp()
        return tools


# ---------------------------------------------------------------------------------------------- hooks

def install():
    apply(load_config())


def save_plugin_config(default=None, settings=None, project_name: str = "", agent_profile: str = "", **kwargs):
    if project_name or agent_profile:
        raise SetupError(f"{TITLE} settings are global; they cannot be saved per project or agent profile")
    try:
        cfg = normalize_config(settings if isinstance(settings, dict) else default)
        _notify("progress", f"Applying the settings: preparing {PRODUCT} at {cfg['server_ref']!r} ...")
        tools = apply(cfg)
    except Exception as e:
        _notify("error", f"Settings not saved. {e}")
        raise
    _notify("success", f"Settings saved: {PRODUCT} at {cfg['server_ref']!r} is registered as {MCP_NAME} ({tools} tools).")
    return {key: cfg[key] for key in DEFAULTS}


def pre_update():
    return None


def uninstall():
    try:
        unregister()
    except Exception as e:  # never block an uninstall; say what is left
        _log(f"could not remove the MCP entry ({e}); remove it under Settings -> MCP/A2A -> External MCP Servers")
