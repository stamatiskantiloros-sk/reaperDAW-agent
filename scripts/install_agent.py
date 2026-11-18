#!/usr/bin/env python3
"""Register (or remove) our Lua handlers in the vendored Reaper Daemon bridge.

Adds the absolute path of lua/agent/handlers.lua to "extra_handler_modules" in
upstream/bridge/bridge_config.json (a gitignored, per-machine file) and, unless --no-reload is
given, asks the running bridge to reload. Requires the hook from patches/0001-extra-handlers.patch,
which is already applied to the vendored bridge in this repo.

    python3 scripts/install_agent.py              # install and reload
    python3 scripts/install_agent.py --uninstall  # remove the entry and reload
    python3 scripts/install_agent.py --dry-run    # show what would change
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "upstream" / "bridge" / "bridge_config.json"
HANDLERS = ROOT / "lua" / "agent" / "handlers.lua"
HOOK_MARKER = "extra_handler_modules"
BRIDGE_LUA = ROOT / "upstream" / "bridge" / "reaper_agent_bridge.lua"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-reload", action="store_true", help="do not send reload_bridge")
    args = parser.parse_args()

    if HOOK_MARKER not in BRIDGE_LUA.read_text(encoding="utf-8"):
        print(
            "The bridge is missing the extra-handlers hook; apply patches/0001-extra-handlers.patch."
        )
        return 1
    if not HANDLERS.exists():
        print(f"missing {HANDLERS}")
        return 1
    if not CONFIG.exists():
        print(
            f"{CONFIG} does not exist yet; start REAPER once with the Reaper Daemon bridge loaded."
        )
        return 1

    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    modules = [m for m in config.get(HOOK_MARKER, []) if m != str(HANDLERS)]
    if not args.uninstall:
        modules.append(str(HANDLERS))
    if modules:
        config[HOOK_MARKER] = modules
    else:
        config.pop(HOOK_MARKER, None)

    verb = "remove" if args.uninstall else "add"
    if args.dry_run:
        print(f"[dry-run] would {verb} {HANDLERS} in {CONFIG}")
        return 0
    CONFIG.write_text(json.dumps(config), encoding="utf-8")
    print(f"{verb}ed handler module in {CONFIG}")

    if args.no_reload:
        print("Reload skipped: send the reload_bridge command or restart REAPER to apply.")
        return 0
    sys.path.insert(0, str(ROOT / "upstream"))
    import reaperd

    reply = reaperd.send_type(
        "reload_bridge", {}, bridge_root=str(ROOT / "upstream"), timeout_ms=15000
    )
    print("reload_bridge:", "ok" if reply.get("ok") else reply.get("error"))
    return 0 if reply.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
