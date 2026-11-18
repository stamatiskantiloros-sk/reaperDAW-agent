-- Shared helpers for the reaper-agent handlers. Loaded by handlers.lua; returns function(ctx) -> U.
-- No file writes, no deletes, no process execution (see tests/unit/test_invariants.py).

return function(ctx)
  local r = ctx.reaper
  local json = ctx.json
  local U = {}

  U.EXT_KEY = "P_EXT:reaper_agent" -- one JSON blob per object, so keys can be enumerated
  U.EXT_PREFIX = "reaper_agent:"

  -- REAPER returns many integers as floats; the Python model wants real integers.
  function U.int(x)
    return math.floor((x or 0) + 0.5)
  end

  function U.round(x, digits)
    local m = 10 ^ (digits or 6)
    return math.floor(x * m + 0.5) / m
  end

  -- "#RRGGBB" <-> REAPER native colour. Native 0 means "default colour" and maps to nil.
  function U.hex_from_native(native)
    if native == nil or native == 0 then return nil end
    local red, green, blue = r.ColorFromNative(native & 0xFFFFFF)
    return string.format("#%02X%02X%02X", red, green, blue)
  end

  function U.native_from_hex(hex)
    if hex == nil then return 0 end
    local red = tonumber(hex:sub(2, 3), 16)
    local green = tonumber(hex:sub(4, 5), 16)
    local blue = tonumber(hex:sub(6, 7), 16)
    return r.ColorToNative(red, green, blue) | 0x1000000
  end

  function U.db_from_linear(vol)
    if not vol or vol <= 0 then return -150.0 end
    return U.round(20 * math.log(vol, 10), 3)
  end

  function U.take_guid(take)
    local ok, guid = r.GetSetMediaItemTakeInfo_String(take, "GUID", "", false)
    return ok and guid or nil
  end

  function U.item_guid(item)
    local ok, guid = r.GetSetMediaItemInfo_String(item, "GUID", "", false)
    return ok and guid or nil
  end

  -- Ext state: a JSON object stored under P_EXT:reaper_agent on tracks, items and takes.
  local getters = {
    track = r.GetSetMediaTrackInfo_String,
    item = r.GetSetMediaItemInfo_String,
    take = r.GetSetMediaItemTakeInfo_String,
  }

  function U.ext_read(kind, obj)
    local ok, text = getters[kind](obj, U.EXT_KEY, "", false)
    if not ok or text == "" then return {} end
    local parsed_ok, parsed = pcall(json.decode, text)
    if not parsed_ok or type(parsed) ~= "table" then return {} end
    return parsed
  end

  -- Same as ext_read but with the model's "reaper_agent:" key prefix, for snapshots.
  function U.ext_for_snapshot(kind, obj)
    local out = {}
    for key, value in pairs(U.ext_read(kind, obj)) do
      out[U.EXT_PREFIX .. key] = tostring(value)
    end
    return out
  end

  function U.ext_write(kind, obj, key, value)
    local blob = U.ext_read(kind, obj)
    blob[key] = value
    return getters[kind](obj, U.EXT_KEY, json.encode(blob), true)
  end

  -- Project ext state lives in section "reaper_agent" and can be enumerated.
  function U.project_ext_for_snapshot()
    local out = {}
    local i = 0
    while true do
      local ok, key, value = r.EnumProjExtState(0, "reaper_agent", i)
      if not ok then break end
      out[U.EXT_PREFIX .. key] = value
      i = i + 1
    end
    return out
  end

  return U
end
