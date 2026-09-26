"""hooks.py: the paper profile, config validation, MCP settings handling, server checkout and rollback,
the smoke test with its paper-safety gate, and the hook entry points.

Agent Zero's framework modules (helpers.settings, helpers.plugins) are replaced by small fakes so the suite
runs anywhere; the real framework path is exercised by the UAT harness against Agent Zero v2.13.
"""

import importlib.util
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from tests.test_contract import EXPECTED_GATE, SERVER_TOOLS

ROOT = Path(__file__).resolve().parents[1]
FAKE_SERVER = Path(__file__).with_name("fake_server.py")
ENTRY = "app/main.py"  # hooks.ENTRYPOINT (checked below)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class FakeSettings(types.ModuleType):
    def __init__(self):
        super().__init__("helpers.settings")
        self.value = {"mcp_servers": '{"mcpServers": {}}'}
        self.writes = []

    def get_settings(self):
        return dict(self.value)

    def set_settings_delta(self, delta, apply=True):
        self.writes.append(delta)
        self.value.update(delta)


@pytest.fixture
def env(monkeypatch, tmp_path):
    settings = FakeSettings()
    plugins = types.ModuleType("helpers.plugins")
    plugins.saved = {}
    plugins.get_plugin_config = lambda name, **kw: plugins.saved
    helpers = types.ModuleType("helpers")
    helpers.settings, helpers.plugins = settings, plugins
    monkeypatch.setitem(sys.modules, "helpers", helpers)
    monkeypatch.setitem(sys.modules, "helpers.settings", settings)
    monkeypatch.setitem(sys.modules, "helpers.plugins", plugins)
    spec = importlib.util.spec_from_file_location("hooks_under_test", ROOT / "hooks.py")
    hooks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hooks)
    monkeypatch.setattr(hooks, "SERVER_DIR", tmp_path / "plugin" / "server")  # never the repository itself
    monkeypatch.setattr(hooks, "DATA_DIR", tmp_path / "plugin" / "data")
    notes = []
    monkeypatch.setattr(hooks, "_notify", lambda kind, message: notes.append((kind, message)))
    return types.SimpleNamespace(hooks=hooks, settings=settings, plugins=plugins, notes=notes)


def servers(env):
    return json.loads(env.settings.value["mcp_servers"])["mcpServers"]


def entry(env, python="/x/python"):
    return env.hooks.mcp_entry(env.hooks.load_config(), Path(python))


# ------------------------------------------------------------------ the paper profile

def test_paper_profile_is_fixed_and_carries_no_credentials(env):
    e = entry(env)["env"]
    assert e["PAPER_MODE"] == "true" and e["LIVE_TRADING_ENABLED"] == "false" and e["TRADING_HALTED"] == "true"
    # ReadyTrader-Stocks holds even paper orders for an operator approval in approve_each mode; live stays
    # closed by the three switches above, and the broker clients stay on their paper/sandbox accounts.
    assert e["EXECUTION_APPROVAL_MODE"] == "auto" and e["ALPACA_PAPER"] == "true" and e["TRADIER_SANDBOX"] == "true"
    assert e["ETRADE_SANDBOX"] == "true"  # opt-in in the server: unset means E*TRADE production
    assert not [k for k in e if any(w in k for w in ("KEY", "SECRET", "TOKEN", "PASSWORD", "PRIVATE"))]


def test_every_data_file_is_pinned_under_both_names_the_server_reads(env):
    e = entry(env)["env"]
    data = str(env.hooks.DATA_DIR)
    assert e["READYTRADER_DATA_DIR"] == data
    for name in env.hooks.DB_FILES:
        for key in (f"{name}_DB_PATH", f"READYTRADER_{name}_DB_PATH"):
            assert e[key] == f"{data}/{name.lower()}.db"


def test_entry_runs_the_server_by_absolute_path(env):
    e = entry(env, "/opt/p/python")
    assert e["type"] == "stdio" and e["command"] == "/opt/p/python"
    assert e["args"] == [str(env.hooks.SERVER_DIR / env.hooks.ENTRYPOINT)]
    assert e["init_timeout"] == 60 and e["tool_timeout"] == 120
    assert env.hooks.MARKER in e["description"]


# ------------------------------------------------------------------ config

def test_config_defaults_and_saved_values(env):
    assert env.hooks.load_config()["server_ref"] == "main"
    env.plugins.saved = {"server_ref": "v0.2.0", "tool_timeout": "300", "init_timeout": None}
    cfg = env.hooks.load_config()
    assert cfg["server_ref"] == "v0.2.0" and cfg["tool_timeout"] == 300 and cfg["init_timeout"] == 60


def test_an_unreadable_config_json_is_an_error_not_the_defaults(env):
    def broken(name, **kw):
        raise ValueError("Expecting value: line 1 column 1")

    env.plugins.get_plugin_config = broken
    with pytest.raises(env.hooks.SetupError, match="config.json"):
        env.hooks.load_config()


def test_the_mcp_name_is_fixed_whatever_the_saved_settings_say(env):
    # The skill calls readytrader_stocks.<tool>; an entry under another name would send those calls elsewhere.
    env.plugins.saved = {"mcp_server_name": "rt_paper"}  # a 2.0.0 pre-release setting, now ignored
    cfg = env.hooks.load_config()
    assert "mcp_server_name" not in cfg
    env.hooks.register(cfg, entry(env))
    assert list(servers(env)) == ["readytrader_stocks"]


@pytest.mark.parametrize("url,ok", [
    ("https://github.com/up2itnow0822/ReadyTrader-Stocks.git", True),
    ("http://github.com/x/y.git", False),
    ("file:///etc", False),
    ("relative/path", False),
    ("git@github.com:x/y.git", False),
    ("ext::sh -c touch% /tmp/x", False),
    ("-uhelp", False),
    ("https://", False),
])
def test_server_repo_must_be_https_or_an_existing_directory(env, url, ok, tmp_path):
    if ok:
        assert env.hooks.check_repo(url) == url
    else:
        with pytest.raises(env.hooks.SetupError):
            env.hooks.check_repo(url)
    assert env.hooks.check_repo(str(tmp_path)) == str(tmp_path)


@pytest.mark.parametrize("ref,ok", [
    ("main", True), ("v0.2.0", True), ("release/2026.09", True), ("0123456789abcdef0123456789abcdef01234567", True),
    ("--upload-pack=touch /tmp/x", False), ("-b", False), ("a..b", False), ("a b", False), ("x.lock", False), ("", False),
])
def test_server_ref_is_a_ref_never_an_option(env, ref, ok):
    if ok:
        assert env.hooks.check_ref(ref) == ref
    else:
        with pytest.raises(env.hooks.SetupError, match="server_ref"):
            env.hooks.check_ref(ref)


@pytest.mark.parametrize("value", ["abc", 0, 4, 3601])
def test_timeouts_must_be_sane_whole_seconds(env, value):
    with pytest.raises(env.hooks.SetupError, match="tool_timeout"):
        env.hooks.normalize_config({"tool_timeout": value})


# ------------------------------------------------------------------ MCP settings shapes

@pytest.mark.parametrize("raw,names", [
    ("", []),
    ("   ", []),
    ('{"mcpServers": {}}', []),
    ('{"mcpServers": {"a": {"command": "x"}}}', ["a"]),
    ('{"mcpServers": [{"name": "a", "command": "x"}]}', ["a"]),
    ('[{"name": "a", "command": "x"}, {"name": "b", "url": "http://y"}]', ["a", "b"]),
    ('{"name": "mine", "command": "npx"}', ["mine"]),
])
def test_every_shape_agent_zero_accepts_is_read(env, raw, names):
    assert list(env.hooks.parse_servers(raw)[0]) == names


@pytest.mark.parametrize("raw", ['["no", "names"]', '[{"command": "x"}]', '[{"name": "a"}, {"name": "a"}]', '42',
                                 '{"mcpServers": {"a": {"command": "x"},}}', '{"mcpServers": {}} // mine',
                                 '{"mcpServers": {"a": {"command": "x"}}}\n{"mcpServers": {"b": {"command": "y"}}}'])
def test_shapes_that_would_lose_a_server_are_refused(env, raw):
    with pytest.raises(env.hooks.SetupError):
        env.hooks.parse_servers(raw)


def test_other_top_level_keys_and_non_ascii_text_are_kept(env):
    env.settings.value["mcp_servers"] = '{"$comment": "mine", "mcpServers": {"notes": {"command": "x", "description": "日本語"}}}'
    env.hooks.register(env.hooks.load_config(), entry(env))
    written = env.settings.value["mcp_servers"]
    assert json.loads(written)["$comment"] == "mine" and "日本語" in written


def test_a_single_server_setting_keeps_that_server(env):
    env.settings.value["mcp_servers"] = '{"name": "mine", "command": "npx", "args": ["m"]}'
    env.hooks.register(env.hooks.load_config(), entry(env))
    assert servers(env)["mine"] == {"command": "npx", "args": ["m"]}
    env.hooks.unregister()
    assert servers(env) == {"mine": {"command": "npx", "args": ["m"]}}


# ------------------------------------------------------------------ registration

def test_register_adds_the_entry_and_keeps_other_servers(env):
    env.settings.value["mcp_servers"] = json.dumps({"mcpServers": {"other": {"command": "npx", "args": ["x"]}}})
    assert env.hooks.register(env.hooks.load_config(), entry(env)) is True
    got = servers(env)
    assert got["other"] == {"command": "npx", "args": ["x"]}
    assert got["readytrader_stocks"] == entry(env)


def test_register_is_idempotent(env):
    cfg = env.hooks.load_config()
    env.hooks.register(cfg, entry(env))
    assert env.hooks.register(cfg, entry(env)) is False
    assert len(env.settings.writes) == 1


@pytest.mark.parametrize("theirs", ["readytrader_stocks", "ReadyTrader-Stocks", " readytrader stocks"])
def test_register_refuses_a_server_agent_zero_would_confuse_with_ours(env, theirs):
    env.settings.value["mcp_servers"] = json.dumps({"mcpServers": {theirs: {"command": "mine", "env": {"PAPER_MODE": "false"}}}})
    with pytest.raises(env.hooks.SetupError, match="(?s)not added by this plugin.*needs that name"):
        env.hooks.register(env.hooks.load_config(), entry(env))
    assert env.settings.writes == []


def test_an_entry_of_ours_under_another_name_moves_to_the_fixed_name(env):
    env.settings.value["mcp_servers"] = json.dumps({"mcpServers": {"rt_stocks": entry(env), "other": {"command": "npx"}}})
    assert env.hooks.register(env.hooks.load_config(), entry(env)) is True
    assert sorted(servers(env)) == ["other", "readytrader_stocks"]


def test_a_users_own_server_running_our_entrypoint_is_left_alone(env):
    theirs = {"command": "/x/python", "args": [str(env.hooks.SERVER_DIR / env.hooks.ENTRYPOINT)], "env": {"RISK_PROFILE": "mine"}}
    env.settings.value["mcp_servers"] = json.dumps({"mcpServers": {"rt_testnet": theirs}})
    env.hooks.register(env.hooks.load_config(), entry(env))
    assert servers(env)["rt_testnet"] == theirs and "RISK_PROFILE" not in servers(env)["readytrader_stocks"]["env"]
    env.hooks.unregister()
    assert servers(env) == {"rt_testnet": theirs}


def test_our_entry_is_recognised_even_if_its_description_was_edited(env):
    env.hooks.register(env.hooks.load_config(), entry(env))
    data = json.loads(env.settings.value["mcp_servers"])
    data["mcpServers"]["readytrader_stocks"]["description"] = "my notes"
    env.settings.value["mcp_servers"] = json.dumps(data)
    assert env.hooks.register(env.hooks.load_config(), entry(env)) is True  # refreshed, not refused
    assert env.hooks.unregister() is True and servers(env) == {}


def test_unregister_removes_only_our_entry(env):
    env.settings.value["mcp_servers"] = json.dumps({"mcpServers": {"other": {"command": "npx"}}})
    env.hooks.register(env.hooks.load_config(), entry(env))
    assert env.hooks.unregister() is True
    assert servers(env) == {"other": {"command": "npx"}}
    assert env.hooks.unregister() is False


def test_broken_mcp_settings_are_never_rewritten(env):
    env.settings.value["mcp_servers"] = '["not", "an", "object"]'
    with pytest.raises(env.hooks.SetupError, match="MCP/A2A"):
        env.hooks.register(env.hooks.load_config(), entry(env))
    assert env.settings.writes == []


def test_uninstall_never_raises_on_broken_mcp_settings(env):
    env.settings.value["mcp_servers"] = '["not", "an", "object"]'
    env.hooks.uninstall()  # Agent Zero must still be able to delete the plugin
    assert env.settings.writes == []


def test_an_update_keeps_proxy_settings_but_nothing_else_the_user_added(env):
    cfg = env.hooks.load_config()
    env.hooks.register(cfg, entry(env))
    data = json.loads(env.settings.value["mcp_servers"])
    mine = data["mcpServers"]["readytrader_stocks"]
    mine["env"].update({
        "HTTPS_PROXY": "http://proxy.example:3128",
        "PAPER_MODE": "false",                       # a hand edit that would turn paper off
        "paper_mode": "false",                       # the same in another case
        "READYTRADER_PAPER_DB_PATH": "/elsewhere/paper.db",
        "ALPACA_API_KEY": "k", "ALPACA_API_SECRET": "s",
        "SIGNER_REMOTE_URL": "https://signer", "ALLOW_TOKENS": "usdc", "no_proxy": "localhost",
        "SSL_CERT_FILE": "/etc/ssl/ca.pem",
    })
    mine["disabled_tools"] = ["run_backtest_simulation"]
    env.settings.value["mcp_servers"] = json.dumps(data)
    env.hooks.register(cfg, entry(env))  # what a refresh does
    after = servers(env)["readytrader_stocks"]["env"]
    assert after["HTTPS_PROXY"] == "http://proxy.example:3128"
    assert after["PAPER_MODE"] == "true" and "paper_mode" not in after
    assert after["READYTRADER_PAPER_DB_PATH"].startswith(str(env.hooks.DATA_DIR))
    assert not [k for k in after if "ALPACA_API" in k or k in ("SIGNER_REMOTE_URL", "ALLOW_TOKENS")]
    assert after["no_proxy"] == "localhost" and after["SSL_CERT_FILE"] == "/etc/ssl/ca.pem"
    assert servers(env)["readytrader_stocks"]["disabled_tools"] == ["run_backtest_simulation"]
    assert any("ALPACA_API_KEY" in message for _, message in env.notes)


# ------------------------------------------------------------------ hook entry points

def test_install_registers_only_after_the_server_passes_the_smoke_test(env, monkeypatch):
    calls = []
    monkeypatch.setattr(env.hooks, "ensure_server", lambda cfg: calls.append("ensure") or (Path("/v/python"), {"head": "abc"}))
    monkeypatch.setattr(env.hooks, "reload_agent_zero_mcp", lambda: calls.append("reload"))
    monkeypatch.setattr(env.hooks, "restore_server", lambda undo: calls.append(("restore", undo["head"])))

    def broken(entry):
        calls.append("smoke")
        raise env.hooks.SetupError("server did not start")

    monkeypatch.setattr(env.hooks, "smoke_test", broken)
    with pytest.raises(env.hooks.SetupError):
        env.hooks.install()
    assert calls == ["ensure", "smoke", ("restore", "abc")] and env.settings.writes == []

    monkeypatch.setattr(env.hooks, "smoke_test", lambda entry: calls.append("smoke") or 29)
    env.hooks.install()
    assert "readytrader_stocks" in servers(env)


def test_saving_settings_applies_them_before_they_are_written(env, monkeypatch):
    applied = []
    monkeypatch.setattr(env.hooks, "apply", lambda cfg: applied.append(cfg) or 29)
    saved = env.hooks.save_plugin_config(settings={"server_ref": "v9.9.9", "tool_timeout": "300", "junk": 1})
    assert applied[0]["server_ref"] == "v9.9.9" and applied[0]["tool_timeout"] == 300
    assert saved == {**env.hooks.DEFAULTS, "server_ref": "v9.9.9", "tool_timeout": 300}
    assert env.notes[-1][0] == "success"


def test_a_setting_that_cannot_be_applied_is_not_saved(env, monkeypatch):
    def fail(cfg):
        raise env.hooks.SetupError("fetching 'nope' failed: couldn't find remote ref nope")

    monkeypatch.setattr(env.hooks, "apply", fail)
    with pytest.raises(env.hooks.SetupError, match="nope"):
        env.hooks.save_plugin_config(settings={"server_ref": "nope"})
    assert env.notes[-1][0] == "error" and "not saved" in env.notes[-1][1]
    with pytest.raises(env.hooks.SetupError, match="server_ref"):
        env.hooks.save_plugin_config(settings={"server_ref": "--upload-pack=x"})
    with pytest.raises(env.hooks.SetupError, match="global"):
        env.hooks.save_plugin_config(settings={}, agent_profile="agent0")


def test_uninstall_unregisters(env):
    env.hooks.register(env.hooks.load_config(), entry(env))
    env.hooks.uninstall()
    assert servers(env) == {}


# ------------------------------------------------------------------ server checkout (real git + venv)

def _git_repo(path):
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    (path / ".gitignore").write_text(".env\n.venv/\n")
    _write(path / ENTRY, "print('server v1')\n")
    (path / "requirements.txt").write_text("")
    return path


def _commit(repo, message):
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", message], check=True)


@pytest.fixture
def plugin(env, monkeypatch, tmp_path):
    repo = _git_repo(tmp_path / "rt")
    _commit(repo, "v1")
    root = tmp_path / "plugin"
    root.mkdir()
    monkeypatch.setattr(env.hooks, "SERVER_DIR", root / "server")
    monkeypatch.setattr(env.hooks, "DATA_DIR", root / "data")
    monkeypatch.setattr(env.hooks, "MIN_PYTHON", (3, 0))
    env.plugins.saved = {"server_repo": str(repo), "server_ref": "main"}
    return types.SimpleNamespace(repo=repo, root=root, server=root / "server")


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_ensure_server_clones_the_ref_builds_a_venv_and_updates(env, plugin):
    py, undo = env.hooks.ensure_server(env.hooks.load_config())
    assert py.exists() and (plugin.server / ENTRY).exists() and (plugin.root / "data").is_dir()
    assert undo["head"] is None
    _write(plugin.repo / ENTRY, "print('server v2')\n")
    _commit(plugin.repo, "v2")
    env.hooks.ensure_server(env.hooks.load_config())  # an update fetches the new commit
    assert "v2" in (plugin.server / ENTRY).read_text()


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_the_checkout_stops_readytrader_reading_agent_zeros_env_file(env, plugin):
    env.hooks.ensure_server(env.hooks.load_config())
    assert (plugin.server / ".env").is_file() and "usr/.env" in (plugin.server / ".env").read_text()
    status = subprocess.run(["git", "-C", str(plugin.server), "status", "--porcelain"], capture_output=True, text=True)
    assert status.stdout == ""  # ignored by the server's own .gitignore; updates stay clean


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_failed_update_puts_the_previous_server_back(env, plugin):
    env.hooks.ensure_server(env.hooks.load_config())
    (plugin.repo / ENTRY).unlink()
    _commit(plugin.repo, "broken: no entrypoint")
    with pytest.raises(env.hooks.SetupError, match=f"has no {ENTRY}"):
        env.hooks.ensure_server(env.hooks.load_config())
    assert "v1" in (plugin.server / ENTRY).read_text()


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_bad_ref_changes_nothing(env, plugin):
    env.hooks.ensure_server(env.hooks.load_config())
    env.plugins.saved["server_ref"] = "no-such-ref"
    with pytest.raises(env.hooks.SetupError, match="fetching 'no-such-ref'"):
        env.hooks.ensure_server(env.hooks.load_config())
    assert "v1" in (plugin.server / ENTRY).read_text()


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_broken_venv_is_rebuilt_and_its_requirements_reinstalled(env, plugin):
    py, _ = env.hooks.ensure_server(env.hooks.load_config())
    stamp = env.hooks._stamp()
    assert stamp.exists()
    py.unlink()  # e.g. Agent Zero's Python changed and the venv's interpreter link now dangles
    py2, _ = env.hooks.ensure_server(env.hooks.load_config())
    assert env.hooks._python_works(py2) and stamp.exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_non_git_folder_in_the_way_is_moved_aside_not_deleted(env, plugin):
    plugin.server.mkdir()
    (plugin.server / "keep.txt").write_text("user file")
    env.hooks.ensure_server(env.hooks.load_config())
    moved = [p for p in plugin.root.iterdir() if p.name.startswith("server.moved-")]
    assert moved and (moved[0] / "keep.txt").read_text() == "user file"


def test_python_older_than_3_12_is_refused_before_building_a_venv(env, monkeypatch, tmp_path):
    repo = tmp_path / "rt"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setattr(env.hooks, "SERVER_DIR", repo)
    monkeypatch.setattr(env.hooks, "_run", lambda *a, **k: "")
    _write(repo / ENTRY, "")
    monkeypatch.setattr(env.hooks, "MIN_PYTHON", (99, 0))
    env.plugins.saved = {"server_repo": str(tmp_path)}
    with pytest.raises(env.hooks.SetupError, match="needs Python"):
        env.hooks.ensure_server(env.hooks.load_config())


# ------------------------------------------------------------------ smoke test and the paper-safety gate

def fake_entry(env, mode, init_timeout=30):
    """The fake offers ReadyTrader-Stocks' own tools (test_contract.SERVER_TOOLS) and answers with the server's own
    paper-mode refusal (EXPECTED_GATE), never what hooks.py asks for: a wrong constant in hooks.py fails here."""
    h = env.hooks
    e = h.mcp_entry(h.normalize_config({"init_timeout": init_timeout}), Path(sys.executable))
    e["args"] = [str(FAKE_SERVER)]
    tool, args, code = EXPECTED_GATE[0]
    e["env"] = {**e["env"], "FAKE_MODE": mode, "FAKE_TOOLS": ",".join(SERVER_TOOLS), "FAKE_GATE": json.dumps([tool, args, code])}
    return e


def test_smoke_test_passes_a_current_server_that_refuses_in_paper_mode(env):
    h = env.hooks
    assert h.ENTRYPOINT == ENTRY
    assert h.smoke_test(fake_entry(env, "current")) == len(SERVER_TOOLS)


def test_smoke_test_refuses_a_server_without_the_paper_mode_refusal(env):
    gate_tool = EXPECTED_GATE[0][0]
    with pytest.raises(env.hooks.SetupError,
                       match=f"did not show that it runs in paper mode: {gate_tool} answered.*{env.hooks.PAPER_GATE_PR}"):
        env.hooks.smoke_test(fake_entry(env, "old"))


def test_smoke_test_refuses_a_server_without_the_skill_s_tools(env):
    with pytest.raises(env.hooks.SetupError, match="does not offer"):
        env.hooks.smoke_test(fake_entry(env, "missing"))


def test_a_crashing_server_is_reported_with_its_own_output(env):
    with pytest.raises(env.hooks.SetupError, match="(?s)starting the server.*No module named 'yfinance'"):
        env.hooks.smoke_test(fake_entry(env, "crash"))


def test_a_silent_server_is_reported_as_a_timeout(env, monkeypatch):
    monkeypatch.setattr(env.hooks, "SMOKE_MIN_SECONDS", 5)
    with pytest.raises(env.hooks.SetupError, match="no answer within 5s"):
        env.hooks.smoke_test(fake_entry(env, "hang", init_timeout=5))


def test_apply_refuses_a_smoke_result_without_proof(env, monkeypatch):
    undone = []
    monkeypatch.setattr(env.hooks, "ensure_server", lambda cfg: (Path("/v/python"), {"head": "abc"}))
    monkeypatch.setattr(env.hooks, "restore_server", lambda undo: undone.append(undo["head"]))
    for result in (None, 0, True, "29"):
        monkeypatch.setattr(env.hooks, "smoke_test", lambda entry, r=result: r)
        with pytest.raises(env.hooks.SetupError, match="no proof"):
            env.hooks.apply(env.hooks.load_config())
    assert undone == ["abc"] * 4 and env.settings.writes == []


def test_a_repair_save_makes_agent_zero_reload_the_server(env, monkeypatch):
    reloads = []
    monkeypatch.setattr(env.hooks, "ensure_server", lambda cfg: (Path("/v/python"), {"head": "abc"}))
    monkeypatch.setattr(env.hooks, "smoke_test", lambda entry: 29)
    monkeypatch.setattr(env.hooks, "reload_agent_zero_mcp", lambda: reloads.append(1))
    env.hooks.apply(env.hooks.load_config())   # first registration: the settings change reloads Agent Zero
    env.hooks.apply(env.hooks.load_config())   # same entry: the plugin asks Agent Zero to reload
    assert len(env.settings.writes) == 1 and reloads == [1]


def test_a_settings_problem_is_found_before_the_server_moves(env, monkeypatch):
    moved = []
    monkeypatch.setattr(env.hooks, "ensure_server", lambda cfg: moved.append(1))
    env.settings.value["mcp_servers"] = '{"mcpServers": {"ReadyTrader Stocks": {"command": "mine"}}}'
    with pytest.raises(env.hooks.SetupError, match="not added by this plugin"):
        env.hooks.apply(env.hooks.load_config())
    env.settings.value["mcp_servers"] = '{"mcpServers": {}} // not strict JSON'
    with pytest.raises(env.hooks.SetupError, match="strict JSON"):
        env.hooks.apply(env.hooks.load_config())
    assert moved == []


def test_notifications_are_escaped_because_agent_zero_renders_them_as_html(env, monkeypatch):
    sent = []
    mod = types.ModuleType("helpers.notification")

    class _T:
        def __init__(self, v):
            self.v = v

    mod.NotificationType, mod.NotificationPriority = _T, types.SimpleNamespace(NORMAL=10)
    mod.NotificationManager = types.SimpleNamespace(send_notification=lambda *a, **k: sent.append(a[2]))
    monkeypatch.setitem(sys.modules, "helpers.notification", mod)
    spec = importlib.util.spec_from_file_location("hooks_notify", ROOT / "hooks.py")
    hooks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hooks)
    hooks._notify("error", "fatal: <img src=x onerror=alert(1)>\nline 2")
    assert sent == ["fatal: &lt;img src=x onerror=alert(1)&gt;<br>line 2"]


def test_setups_are_serialised_across_module_copies(env, tmp_path):
    import threading
    import time as _time

    def load_copy(tag):
        spec = importlib.util.spec_from_file_location(f"hooks_copy_{tag}", ROOT / "hooks.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        m.SERVER_DIR = tmp_path / "shared" / "server"
        return m

    a, b = load_copy("a"), load_copy("b")
    order = []

    def hold():
        with a._setup_lock():
            order.append("a in")
            _time.sleep(1.0)
            order.append("a out")

    t = threading.Thread(target=hold)
    t.start()
    _time.sleep(0.3)
    with b._setup_lock():
        order.append("b in")
    t.join()
    assert order == ["a in", "a out", "b in"]


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_registration_failure_rolls_the_checkout_back(env, plugin, monkeypatch):
    monkeypatch.setattr(env.hooks, "smoke_test", lambda entry: 6)
    env.hooks.apply(env.hooks.load_config())
    _write(plugin.repo / ENTRY, "print('server v2')\n")
    _commit(plugin.repo, "v2")

    def fail(cfg, entry):
        raise env.hooks.SetupError("settings changed under us")

    monkeypatch.setattr(env.hooks, "register", fail)
    with pytest.raises(env.hooks.SetupError, match="changed under us"):
        env.hooks.apply(env.hooks.load_config())
    assert "v1" in (plugin.server / ENTRY).read_text()


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_refused_fresh_checkout_is_moved_aside_not_left_behind_our_entry(env, plugin, monkeypatch):
    monkeypatch.setattr(env.hooks, "smoke_test", lambda entry: 6)
    env.hooks.apply(env.hooks.load_config())
    shutil.rmtree(plugin.server / ".git")  # e.g. restored from a backup without .git

    def gate(entry):
        raise env.hooks.SetupError("does not refuse the gate tool in paper mode")

    monkeypatch.setattr(env.hooks, "smoke_test", gate)
    with pytest.raises(env.hooks.SetupError, match="does not refuse"):
        env.hooks.apply(env.hooks.load_config())
    names = sorted(p.name.split("-")[0] for p in plugin.root.iterdir() if p.name.startswith("server"))
    assert not plugin.server.exists() and names == ["server.failed", "server.moved"]


def test_smoke_test_times_out_inside_a_running_patched_event_loop(env, monkeypatch):
    # Agent Zero applies nest_asyncio and calls hooks from inside its running loop; the check must still fail closed.
    nest_asyncio = pytest.importorskip("nest_asyncio")
    import asyncio

    nest_asyncio.apply()
    monkeypatch.setattr(env.hooks, "SMOKE_MIN_SECONDS", 5)

    async def inside_agent_zero():
        return env.hooks.smoke_test(fake_entry(env, "hang", init_timeout=5))

    with pytest.raises(env.hooks.SetupError, match="no answer within 5s"):
        asyncio.run(inside_agent_zero())


def test_undecodable_server_output_does_not_hide_the_error(env):
    with pytest.raises(env.hooks.SetupError, match="(?s)No module named 'yfinance'"):
        env.hooks.smoke_test(fake_entry(env, "crashbytes"))
