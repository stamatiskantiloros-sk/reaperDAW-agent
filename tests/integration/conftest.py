"""Integration test setup: needs REAPER running with the patched Reaper Daemon bridge and our
handlers installed (`python scripts/install_agent.py`). macOS only for loading fixtures.

Every test starts from a fresh copy of tests/fixtures/generated/small_routed.rpp opened in its
own new REAPER project tab, so it never touches a real project. Tests must leave the project as
they found it (apply -> undo); the tab is left open for you to close without saving.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest

from agent.bridge_client.daemon import DaemonBridge
from agent.bridge_client.protocol import BridgeError

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "generated" / "small_routed.rpp"


def _bridge_or_none():
    try:
        bridge = DaemonBridge(timeout_ms=5000)
        info = bridge.status()
        return bridge if "agent.apply_plan" in info.get("handlers", []) else None
    except Exception:  # noqa: BLE001  (any failure means "bridge not usable")
        return None


def pytest_collection_modifyitems(config, items):
    if _bridge_or_none() is not None:
        return
    skip = pytest.mark.skip(reason="REAPER bridge with reaper-agent handlers not reachable")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def bridge():
    bridge = _bridge_or_none()
    if bridge is None:
        pytest.skip("REAPER bridge with reaper-agent handlers not reachable")
    return bridge


@pytest.fixture(scope="session")
def project(bridge):
    """Open a fresh copy of the small_routed fixture in a new tab and return its snapshot."""
    if sys.platform != "darwin":
        pytest.skip("fixture loading is implemented for macOS only")
    if not FIXTURE.exists():
        subprocess.run([sys.executable, str(ROOT / "scripts" / "make_fixtures.py")], check=True)
    tmp = Path(tempfile.mkdtemp(prefix="reaper_agent_it_"))
    name = f"small_routed_{uuid.uuid4().hex[:8]}.rpp"
    shutil.copy(FIXTURE, tmp / name)
    bridge._send("new_project_tab")
    subprocess.run(["open", "-a", "REAPER", str(tmp / name)], check=True)
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            snap = bridge.snapshot()
        except BridgeError:
            snap = None
        if snap is not None and snap.project.name == name:
            return snap
        time.sleep(0.5)
    pytest.fail("fixture project did not load in REAPER within 30 s")
