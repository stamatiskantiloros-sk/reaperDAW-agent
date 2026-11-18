-- Take, lane, region and ext-state ops: set_take_name, set_track_lane_mode, set_item_lane,
-- set_lane_name, add_region, set_region, set_ext_state. Each op_<type>(op, S) mutates REAPER and
-- returns an optional data table; it raises an error string on failure. S is the per-apply state
-- from apply_plan.lua. Loaded by handlers.lua; returns function(ctx, U) -> table of op_* functions.
-- Semantics match docs/tasks/1.1.md (the fake bridge).

return function(ctx, U)
  local r = ctx.reaper
  local M = {}

  -- REAPER 7: I_FREEMODE 0 = normal, 1 = free item positioning, 2 = fixed lanes.
  local LANE_MODES = { normal = 0, free = 1, fixed_lanes = 2 }
  local LANE_MODE_NAMES = { [0] = "normal", [1] = "free", [2] = "fixed_lanes" }

  local function lane_mode_name(tr)
    return LANE_MODE_NAMES[U.int(r.GetMediaTrackInfo_Value(tr, "I_FREEMODE"))] or "normal"
  end

  local function lane_count(tr)
    return U.int(r.GetMediaTrackInfo_Value(tr, "I_NUMFIXEDLANES"))
  end

  function M.op_set_take_name(op, S)
    local take = S.take(op.take)
    local _, name = r.GetSetMediaItemTakeInfo_String(take, "P_NAME", "", false)
    S.save_original("take", take, "orig_name", name)
    r.GetSetMediaItemTakeInfo_String(take, "P_NAME", op.name, true)
  end

  function M.op_set_track_lane_mode(op, S)
    local tr = S.track(op.track)
    local number = LANE_MODES[op.mode]
    if number == nil then error("bad lane mode " .. tostring(op.mode)) end
    S.save_original("track", tr, "orig_lane_mode", lane_mode_name(tr))
    r.SetMediaTrackInfo_Value(tr, "I_FREEMODE", number)
    r.UpdateTimeline() -- documented as required after changing I_FREEMODE
  end

  function M.op_set_item_lane(op, S)
    local item = S.item(op.item)
    local tr = r.GetMediaItemTrack(item)
    if lane_mode_name(tr) ~= "fixed_lanes" then
      error("track of item " .. tostring(op.item) .. " is not in fixed_lanes mode")
    end
    S.save_original("item", item, "orig_lane", U.int(r.GetMediaItemInfo_Value(item, "I_FIXEDLANE")))
    if op.lane >= lane_count(tr) then
      r.SetMediaTrackInfo_Value(tr, "I_NUMFIXEDLANES", op.lane + 1)
    end
    r.SetMediaItemInfo_Value(item, "I_FIXEDLANE", op.lane)
  end

  function M.op_set_lane_name(op, S)
    local tr = S.track(op.track)
    if lane_mode_name(tr) ~= "fixed_lanes" then
      error("track " .. tostring(op.track) .. " is not in fixed_lanes mode")
    end
    if op.lane >= lane_count(tr) then -- pad so the lane exists
      r.SetMediaTrackInfo_Value(tr, "I_NUMFIXEDLANES", op.lane + 1)
    end
    local ok = r.GetSetMediaTrackInfo_String(tr, "P_LANENAME:" .. op.lane, op.name, true)
    if not ok then error("could not set the name of lane " .. op.lane) end
  end

  -- Regions and markers. Identity is the GUID (MARKER_GUID:<enumeration index>), never the number.
  local function each_marker(visit)
    local i = 0
    while true do
      local ok, is_region, position, region_end, name, number, color = r.EnumProjectMarkers3(0, i)
      if not ok or ok == 0 then break end
      local _, guid = r.GetSetProjectInfo_String(0, "MARKER_GUID:" .. i, "", false)
      local stop = visit({
        guid = guid, is_region = is_region and true or false, position = position,
        region_end = region_end, name = name, number = U.int(number), color = color,
      })
      if stop then break end
      i = i + 1
    end
  end

  local function find_marker(guid)
    local found = nil
    each_marker(function(m)
      if m.guid:upper() == guid:upper() then found = m; return true end
    end)
    return found
  end

  function M.op_add_region(op, S)
    local max_number = 0
    each_marker(function(m)
      if m.number > max_number then max_number = m.number end
    end)
    local wanted = max_number + 1
    local color = op.color and U.native_from_hex(op.color) or 0
    local ret = r.AddProjectMarker2(0, true, op.start, op["end"], op.name, wanted, color)
    if not ret or ret < 0 then error("could not add region") end
    local created = nil
    each_marker(function(m)
      if m.is_region and m.number == U.int(ret) then created = m; return true end
    end)
    if not created then error("added region not found") end
    S.define_ref("ref:" .. op.ref, created.guid)
    return { guid = created.guid }
  end

  function M.op_set_region(op, S)
    local guid = S.resolve(op.region)
    local m = find_marker(guid)
    if not m then error("unknown region " .. tostring(op.region)) end
    if not m.is_region then error("target " .. tostring(op.region) .. " is a marker, not a region") end
    local new_start = op.start ~= nil and op.start or m.position
    local new_end = op["end"] ~= nil and op["end"] or m.region_end
    if new_end <= new_start then error("region end must be greater than start") end
    -- Save the original once, as JSON, in project ext state.
    local key = "orig_region:" .. m.guid
    local ok_saved, saved = r.GetProjExtState(0, "reaper_agent", key)
    if ok_saved == 0 or saved == "" then
      r.SetProjExtState(0, "reaper_agent", key, ctx.json.encode({
        name = m.name, start = U.round(m.position), ["end"] = U.round(m.region_end),
        color = U.hex_from_native(m.color) or "",
      }))
    end
    local name = op.name ~= nil and op.name or m.name
    local flags = (op.name ~= nil and op.name == "") and 1 or 0 -- flags&1 clears the name
    local color = op.color ~= nil and U.native_from_hex(op.color) or 0 -- 0 = leave unchanged
    if not r.SetProjectMarker4(0, m.number, true, new_start, new_end, name, color, flags) then
      error("could not change region " .. tostring(op.region))
    end
  end

  function M.op_set_ext_state(op, S)
    if op.scope == "project" then
      r.SetProjExtState(0, "reaper_agent", op.key, op.value)
      return
    end
    local obj
    if op.scope == "track" then
      obj = S.track(op.target)
    elseif op.scope == "item" then
      obj = S.item(op.target)
    elseif op.scope == "take" then
      obj = S.take(op.target)
    else
      error("bad ext scope " .. tostring(op.scope))
    end
    if not U.ext_write(op.scope, obj, op.key, op.value) then
      error("could not write ext state " .. tostring(op.key))
    end
  end

  return M
end
