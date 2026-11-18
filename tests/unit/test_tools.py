from __future__ import annotations

import json
import re

from agent.core.hashing import snapshot_hash
from agent.server import build_server
from tests.unit.factories import guid
from tests.unit.server_stubs import STORE_API, StubBridge, StubStore, call, text_of


def setup(profile="core"):
    bridge, store = StubBridge(), StubStore()
    return bridge, store, build_server(bridge, store, profile, store_api=STORE_API)


def plan_id_of(text: str) -> str:
    return re.search(r"Plan (\w+):", text).group(1)


def test_overview_happy_and_bridge_down():
    bridge, _, server = setup()
    res = call(server, "get_project_overview")
    text = text_of(res)
    assert "isError" not in res
    assert "demo.rpp" in text and "Tracks: 5" in text and "Regions: 2" in text
    assert snapshot_hash(bridge.snap) in text
    bridge.alive = False
    res = call(server, "get_project_overview")
    assert res["isError"] and "bridge" in text_of(res)


def test_get_snapshot_paging():
    _, _, server = setup()
    data = json.loads(text_of(call(server, "get_snapshot", {"section": "tracks", "limit": 2})))
    assert data["total"] == 5 and len(data["data"]) == 2 and data["next_offset"] == 2
    data = json.loads(
        text_of(call(server, "get_snapshot", {"section": "tracks", "limit": 2, "offset": 4}))
    )
    assert len(data["data"]) == 1 and "next_offset" not in data
    data = json.loads(text_of(call(server, "get_snapshot", {"section": "project"})))
    assert data["data"]["name"] == "demo.rpp"
    assert json.loads(text_of(call(server, "get_snapshot", {"section": "markers"})))["total"] == 3


def test_get_snapshot_errors():
    _, _, server = setup()
    assert call(server, "get_snapshot", {"section": "bogus"})["isError"]
    assert call(server, "get_snapshot", {})["isError"]
    assert call(server, "get_snapshot", {"section": "tracks", "limit": 0})["isError"]
    assert call(server, "get_snapshot", {"section": "tracks", "limit": True})["isError"]


def test_rename_plan_apply_undo_flow():
    bridge, _, server = setup()
    res = call(server, "plan_rename_track", {"track": guid(4), "new_name": "Bass DI 2"})
    text = text_of(res)
    assert "isError" not in res
    assert "Base hash" in text and "Bass DI 2" in text and "Nothing has changed" in text
    pid = plan_id_of(text)
    assert bridge.snap.track(guid(4)).name == "Bass DI"  # planning changed nothing

    dry = text_of(call(server, "apply_plan", {"plan_id": pid, "dry_run": True}))
    assert "Dry run" in dry and bridge.snap.track(guid(4)).name == "Bass DI"

    done = call(server, "apply_plan", {"plan_id": pid})
    assert "Applied plan" in text_of(done) and "isError" not in done
    assert bridge.snap.track(guid(4)).name == "Bass DI 2"

    assert "Undid" in text_of(call(server, "undo_last_plan"))
    assert bridge.undos == 1
    assert "Nothing to undo" in text_of(call(server, "undo_last_plan"))


def test_plan_errors():
    _, _, server = setup()
    res = call(server, "plan_rename_track", {"track": guid(99), "new_name": "x"})
    assert res["isError"] and "cannot plan" in text_of(res)
    res = call(server, "plan_rename_track", {"track": guid(1), "new_name": "x" * 300})
    assert res["isError"] and "cannot plan" in text_of(res)
    assert call(server, "plan_rename_track", {"track": guid(1)})["isError"]


def test_integer_track_index_rejected():
    _, store, server = setup()
    for bad in (3, "3", 0):
        res = call(server, "plan_rename_track", {"track": bad, "new_name": "x"})
        assert res["isError"] and "GUID" in text_of(res) and "indices" in text_of(res)
    res = call(server, "plan_rename_track", {"track": "Kick", "new_name": "x"})
    assert res["isError"] and "GUID" in text_of(res)
    assert store.list() == []


def test_stale_plan_message():
    bridge, _, server = setup()
    pid = plan_id_of(
        text_of(call(server, "plan_rename_track", {"track": guid(4), "new_name": "New"}))
    )
    bridge.snap.track(guid(5)).name = "Changed by the user"
    res = call(server, "apply_plan", {"plan_id": pid})
    assert res["isError"] and "stale" in text_of(res) and "Re-plan" in text_of(res)
    assert bridge.applied == []


def test_apply_errors():
    bridge, _, server = setup()
    res = call(server, "apply_plan", {"plan_id": "nope"})
    assert res["isError"] and "unknown plan" in text_of(res)
    pid = plan_id_of(
        text_of(call(server, "plan_rename_track", {"track": guid(4), "new_name": "New"}))
    )
    bridge.fail_ops = True
    res = call(server, "apply_plan", {"plan_id": pid})
    assert res["isError"] and "rolled back" in text_of(res) and "boom" in text_of(res)


def test_discard_plan():
    _, store, server = setup()
    pid = plan_id_of(
        text_of(call(server, "plan_rename_track", {"track": guid(4), "new_name": "New"}))
    )
    assert "Discarded" in text_of(call(server, "discard_plan", {"plan_id": pid}))
    assert store.list() == []
    res = call(server, "discard_plan", {"plan_id": pid})
    assert res["isError"] and "unknown plan" in text_of(res)


def test_full_profile_list_and_get_plan():
    _, _, server = setup("full")
    assert text_of(call(server, "list_plans")) == "No plans."
    pid = plan_id_of(
        text_of(call(server, "plan_rename_track", {"track": guid(4), "new_name": "New"}))
    )
    assert pid in text_of(call(server, "list_plans"))
    shown = text_of(call(server, "get_plan", {"plan_id": pid}))
    assert "set_track_name" in shown and "Base hash" in shown
    assert call(server, "get_plan", {"plan_id": "nope"})["isError"]


def test_tool_crash_becomes_error_result():
    bridge, _, server = setup()

    def boom():
        raise RuntimeError("kaput")

    bridge.snapshot = boom  # type: ignore[method-assign]
    res = call(server, "get_snapshot", {"section": "tracks"})
    assert res["isError"] and "kaput" in text_of(res)
