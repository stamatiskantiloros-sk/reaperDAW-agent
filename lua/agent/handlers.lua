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

  -- Op handlers live in ops_*.lua; each file returns function(ctx, U) -> { op_<type> = fn }.
  local registry = {}
  for _, name in ipairs({ "ops_track", "ops_take", "ops_fx" }) do
    if loadfile(base .. "/" .. name .. ".lua") then
      for key, fn in pairs(load_module(name)(ctx, util)) do
        if key:match("^op_") then registry[key:sub(4)] = fn end
      end
    end
  end
  local apply_plan = load_module("apply_plan")(ctx, util, registry)

  -- Reads: no undo block (the bridge would otherwise record an empty one).
  ctx.no_undo_block["agent.snapshot"] = true
  ctx.no_undo_block["agent.ping"] = true
  -- apply_plan opens and names its own undo block (the plan summary), so the bridge must not
  -- wrap it in a second one.
  ctx.no_undo_block["agent.apply_plan"] = true

  ctx.handlers["agent.ping"] = function()
    local ops = {}
    for op_type in pairs(registry) do ops[#ops + 1] = op_type end
    table.sort(ops)
    return { version = M.VERSION, ops = ops, handlers = { "agent.ping", "agent.snapshot", "agent.apply_plan", "agent.undo" } }
  end

  ctx.handlers["agent.snapshot"] = function()
    return snapshot()
  end

  ctx.handlers["agent.apply_plan"] = apply_plan

  -- Undo exactly one REAPER undo step (ours or the user's). Used by undo_last_plan.
  ctx.no_undo_block["agent.undo"] = true
  ctx.handlers["agent.undo"] = function()
    local label = ctx.reaper.Undo_CanUndo2(0)
    if label == nil or label == "" then return { undone = false } end
    ctx.reaper.Undo_DoUndo2(0)
    return { undone = true, label = label }
  end
end

return M
