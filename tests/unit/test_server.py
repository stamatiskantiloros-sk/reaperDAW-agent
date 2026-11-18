from __future__ import annotations

import io
import json

import pytest

from agent.server import PROFILES, build_server, main
from tests.unit.server_stubs import STORE_API, StubBridge, StubStore

CORE = {
    "get_project_overview",
    "get_snapshot",
    "plan_rename_track",
    "apply_plan",
    "discard_plan",
    "undo_last_plan",
}


def make(profile="core"):
    return build_server(StubBridge(), StubStore(), profile, store_api=STORE_API)


def rpc(server, method, params=None, mid=1):
    msg = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        msg["params"] = params
    return server.handle_message(msg)


def test_initialize_echoes_supported_version():
    resp = rpc(make(), "initialize", {"protocolVersion": "2024-11-05"})
    assert resp["result"]["protocolVersion"] == "2024-11-05"
    assert resp["result"]["capabilities"] == {"tools": {}}
    assert resp["result"]["serverInfo"]["name"] == "reaper-agent"


def test_initialize_falls_back_on_unknown_version():
    resp = rpc(make(), "initialize", {"protocolVersion": "1999-01-01"})
    assert resp["result"]["protocolVersion"] == "2025-06-18"
    assert rpc(make(), "initialize")["result"]["protocolVersion"] == "2025-06-18"


def test_initialized_notification_gets_no_reply():
    assert make().handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_ping():
    assert rpc(make(), "ping")["result"] == {}


def test_tools_list_core_and_full():
    core = {t["name"] for t in rpc(make("core"), "tools/list")["result"]["tools"]}
    full = {t["name"] for t in rpc(make("full"), "tools/list")["result"]["tools"]}
    assert core == CORE
    assert full == CORE | {"list_plans", "get_plan"}
    assert len(full) < 30


def test_tool_descriptions_short_and_schemas_valid():
    for tool in rpc(make("full"), "tools/list")["result"]["tools"]:
        assert 0 < len(tool["description"]) < 200
        assert tool["inputSchema"]["type"] == "object"
        assert set(tool["inputSchema"]["required"]) <= set(tool["inputSchema"]["properties"])


def test_unknown_method_and_bad_params():
    assert rpc(make(), "nope")["error"]["code"] == -32601
    assert rpc(make(), "tools/call", {"name": 5})["error"]["code"] == -32602
    assert rpc(make(), "tools/call", {"name": "missing"})["error"]["code"] == -32602
    assert (
        rpc(make(), "tools/call", {"name": "apply_plan", "arguments": []})["error"]["code"]
        == -32602
    )
    assert (
        make().handle_message({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": 3})["error"][
            "code"
        ]
        == -32602
    )


def test_full_only_tool_not_callable_in_core():
    assert rpc(make("core"), "tools/call", {"name": "list_plans"})["error"]["code"] == -32602


def test_unknown_profile_rejected():
    with pytest.raises(ValueError):
        build_server(StubBridge(), StubStore(), "bogus")
    assert PROFILES == ("core", "full")


def test_serve_stdout_is_only_json_lines():
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "get_project_overview", "arguments": {}},
        },
    ]
    stdin = io.StringIO("\n".join(json.dumps(m) for m in lines) + "\n\nnot json\n[1]\n")
    stdout = io.StringIO()
    make().serve(stdin, stdout)
    out = stdout.getvalue().splitlines()
    parsed = [json.loads(line) for line in out]  # every line must be JSON
    assert [p.get("id") for p in parsed] == [1, 2, 3, None, None]
    assert parsed[3]["error"]["code"] == -32700
    assert parsed[4]["error"]["code"] == -32600


def test_main_rejects_bad_profile(monkeypatch, capsys):
    monkeypatch.setenv("REAPER_AGENT_PROFILE", "huge")
    assert main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "REAPER_AGENT_PROFILE" in captured.err


def test_main_without_real_bridge_fails_clearly(monkeypatch, capsys):
    import importlib.util

    if importlib.util.find_spec("agent.bridge_client.daemon") is not None:
        pytest.skip("real bridge exists")
    monkeypatch.setenv("REAPER_AGENT_PROFILE", "core")
    assert main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "not available" in captured.err
