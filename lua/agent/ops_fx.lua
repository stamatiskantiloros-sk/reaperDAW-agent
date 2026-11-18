-- FX parameter and FX envelope ops: set_fx_param, ensure_fx_envelope, clear_envelope_range,
-- insert_envelope_points. Each op_<type>(op, S) mutates REAPER and returns an optional data table;
-- it raises an error string on failure. S is the per-apply state from apply_plan.lua.
-- Loaded by handlers.lua; returns function(ctx, U) -> table of op_* functions.
-- This is the only agent file allowed to delete envelope points (clear_envelope_range).

return function(ctx, U)
  local r = ctx.reaper
  local M = {}

  -- InsertEnvelopePointEx shapes: 0 linear, 1 square, 2 slow start/end, 3 fast start,
  -- 4 fast end, 5 bezier.
  local SHAPES = { linear = 0, square = 1, slow = 2, fast_start = 3, fast_end = 4, bezier = 5 }

  -- Index of the FX with this instance GUID on the track (0-based), or an error.
  local function fx_index(tr, guid)
    local wanted = tostring(guid):upper()
    for i = 0, r.TrackFX_GetCount(tr) - 1 do
      local g = r.TrackFX_GetFXGUID(tr, i)
      if g and g:upper() == wanted then return i end
    end
    error("FX " .. tostring(guid) .. " is not on this track")
  end

  -- Shortest decimal text that reads back as the same double (like Python's repr).
  local function repr(x)
    for digits = 15, 17 do
      local text = string.format("%." .. digits .. "g", x)
      if tonumber(text) == x then return text end
    end
    return tostring(x)
  end

  local function find_envelope(tr, index, param)
    return r.GetFXEnvelope(tr, index, param, false)
  end

  local function require_envelope(tr, op)
    local index = fx_index(tr, op.fx)
    local env = find_envelope(tr, index, op.param)
    if not env then
      error("envelope for FX " .. tostring(op.fx) .. " param " .. tostring(op.param) .. " does not exist")
    end
    return env
  end

  function M.op_set_fx_param(op, S)
    local tr = S.track(op.track)
    local index = fx_index(tr, op.fx)
    if op.param >= r.TrackFX_GetNumParams(tr, index) then
      error("FX " .. tostring(op.fx) .. " has no parameter " .. tostring(op.param))
    end
    local before = r.TrackFX_GetParamNormalized(tr, index, op.param)
    S.save_original("track", tr, "orig_fxparam:" .. op.fx .. ":" .. op.param, repr(before))
    if not r.TrackFX_SetParamNormalized(tr, index, op.param, op.normalized) then
      error("could not set parameter " .. tostring(op.param))
    end
    return { before = before, after = r.TrackFX_GetParamNormalized(tr, index, op.param) }
  end

  function M.op_ensure_fx_envelope(op, S)
    local tr = S.track(op.track)
    local index = fx_index(tr, op.fx)
    if find_envelope(tr, index, op.param) then
      return { created = false }
    end
    local env = r.GetFXEnvelope(tr, index, op.param, true)
    if not env then error("could not create the envelope for parameter " .. tostring(op.param)) end
    r.GetSetEnvelopeInfo_String(env, "ACTIVE", "1", true)
    r.GetSetEnvelopeInfo_String(env, "VISIBLE", "1", true)
    r.TrackList_AdjustWindows(false)
    return { created = true }
  end

  function M.op_clear_envelope_range(op, S)
    local env = require_envelope(S.track(op.track), op)
    local before = r.CountEnvelopePointsEx(env, -1)
    if not r.DeleteEnvelopePointRangeEx(env, -1, op.start, op["end"]) then
      error("could not clear the envelope range")
    end
    return { removed = before - r.CountEnvelopePointsEx(env, -1) }
  end

  function M.op_insert_envelope_points(op, S)
    local env = require_envelope(S.track(op.track), op)
    for _, point in ipairs(op.points) do
      local shape = SHAPES[point.shape or "linear"]
      if shape == nil then error("bad envelope shape " .. tostring(point.shape)) end
      if not r.InsertEnvelopePointEx(env, -1, point.time, point.value, shape, 0, false, true) then
        error("could not insert a point at " .. tostring(point.time))
      end
    end
    r.Envelope_SortPointsEx(env, -1)
    return { inserted = #op.points, point_count = r.CountEnvelopePointsEx(env, -1) }
  end

  return M
end
