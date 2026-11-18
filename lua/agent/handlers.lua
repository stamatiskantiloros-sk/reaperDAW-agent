-- Entry point loaded by the patched Reaper Daemon bridge (see patches/0001-extra-handlers.patch).
-- scripts/install_agent.py adds this file's absolute path to "extra_handler_modules" in
-- upstream/bridge/bridge_config.json.

local M = {}

M.VERSION = "0.1.0"

local function dir_of(path)
  return path:match("^(.*)[/\\][^/\\]+$") or "."
end

function M.register(ctx)
  local base = dir_of(ctx.module_path)

  local function load_module(name)
    local chunk, err = loadfile(base .. "/" .. name .. ".lua")
    if not chunk then error("cannot load " .. name .. ": " .. tostring(err)) end
    return chunk()
  end

  local util = load_module("util")(ctx)
  local snapshot = load_module("snapshot")(ctx, util)

  -- Reads: no undo block (the bridge would otherwise record an empty one).
  ctx.no_undo_block["agent.snapshot"] = true
  ctx.no_undo_block["agent.ping"] = true

  ctx.handlers["agent.ping"] = function()
    return { version = M.VERSION, handlers = { "agent.ping", "agent.snapshot" } }
  end

  ctx.handlers["agent.snapshot"] = function()
    return snapshot()
  end
end

return M
