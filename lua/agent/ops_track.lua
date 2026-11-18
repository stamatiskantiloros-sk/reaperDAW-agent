-- Track ops: set_track_name, set_track_color, set_folder_depth, move_track, insert_track,
-- set_track_automation_mode. Each op_<type>(op, S) mutates REAPER and returns an optional data
-- table; it raises an error string on failure. S is the per-apply state from apply_plan.lua.
-- Loaded by handlers.lua; returns function(ctx, U) -> table of op_* functions.

return function(ctx, U)
  local r = ctx.reaper
  local M = {}

  local AUTOMATION = { trim_read = 0, read = 1, touch = 2, write = 3, latch = 4, latch_preview = 5 }
  local AUTOMATION_NAMES = {}
  for name, number in pairs(AUTOMATION) do AUTOMATION_NAMES[number] = name end

  local function track_name(tr)
    local _, name = r.GetSetMediaTrackInfo_String(tr, "P_NAME", "", false)
    return name
  end

  function M.op_set_track_name(op, S)
    local tr = S.track(op.track)
    S.save_original("track", tr, "orig_name", track_name(tr))
    r.GetSetMediaTrackInfo_String(tr, "P_NAME", op.name, true)
  end

  function M.op_set_track_color(op, S)
    local tr = S.track(op.track)
    S.save_original("track", tr, "orig_color", U.hex_from_native(r.GetTrackColor(tr)) or "")
    r.SetMediaTrackInfo_Value(tr, "I_CUSTOMCOLOR", U.native_from_hex(op.color))
  end

  function M.op_set_folder_depth(op, S)
    local tr = S.track(op.track)
    S.save_original("track", tr, "orig_folder_depth", U.int(r.GetMediaTrackInfo_Value(tr, "I_FOLDERDEPTH")))
    r.SetMediaTrackInfo_Value(tr, "I_FOLDERDEPTH", op.depth)
  end

  function M.op_move_track(op, S)
    local tr = S.track(op.track)
    local number = U.int(r.GetMediaTrackInfo_Value(tr, "IP_TRACKNUMBER")) -- 1-based
    local previous = number > 1 and r.GetTrack(0, number - 2) or nil
    S.save_original("track", tr, "orig_prev", previous and r.GetTrackGUID(previous) or "")
    local target_index
    if op.before == nil then
      target_index = r.CountTracks(0)
    else
      local before = S.track(op.before)
      target_index = U.int(r.GetMediaTrackInfo_Value(before, "IP_TRACKNUMBER")) - 1
    end
    r.Main_OnCommand(40297, 0) -- unselect all tracks
    r.SetTrackSelected(tr, true)
    r.ReorderSelectedTracks(target_index, 0)
  end

  function M.op_insert_track(op, S)
    local index
    if op.before == nil then
      index = r.CountTracks(0)
    else
      index = U.int(r.GetMediaTrackInfo_Value(S.track(op.before), "IP_TRACKNUMBER")) - 1
    end
    r.InsertTrackAtIndex(index, true)
    local tr = r.GetTrack(0, index)
    r.GetSetMediaTrackInfo_String(tr, "P_NAME", op.name, true)
    if op.color ~= nil then
      r.SetMediaTrackInfo_Value(tr, "I_CUSTOMCOLOR", U.native_from_hex(op.color))
    end
    local guid = S.add_track(tr)
    S.define_ref("ref:" .. op.ref, guid)
    return { guid = guid }
  end

  function M.op_set_track_automation_mode(op, S)
    local tr = S.track(op.track)
    S.save_original("track", tr, "orig_automation_mode", AUTOMATION_NAMES[r.GetTrackAutomationMode(tr)] or "trim_read")
    local number = AUTOMATION[op.mode]
    if number == nil then error("bad automation mode " .. tostring(op.mode)) end
    r.SetTrackAutomationMode(tr, number)
  end

  return M
end
