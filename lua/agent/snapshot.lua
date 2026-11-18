-- agent.snapshot: read the whole project in one call, in the shape of agent/core/models.py
-- (schema_version 1). Read-only. Loaded by handlers.lua; returns function(ctx, U) -> function().

return function(ctx, U)
  local r = ctx.reaper

  local LANE_MODES = { [0] = "normal", [1] = "free", [2] = "fixed_lanes" }
  local AUTOMATION_MODES = {
    [0] = "trim_read", [1] = "read", [2] = "touch", [3] = "write", [4] = "latch", [5] = "latch_preview",
  }

  local function track_guid(tr)
    return r.GetTrackGUID(tr)
  end

  local function read_sends(tr)
    local sends = {}
    for i = 0, r.GetTrackNumSends(tr, 0) - 1 do
      local dest = r.GetTrackSendInfo_Value(tr, 0, i, "P_DESTTRACK")
      if dest then
        sends[#sends + 1] = {
          dest = track_guid(dest),
          volume_db = U.db_from_linear(r.GetTrackSendInfo_Value(tr, 0, i, "D_VOL")),
          pre_fader = U.int(r.GetTrackSendInfo_Value(tr, 0, i, "I_SENDMODE")) ~= 0,
        }
      end
    end
    return sends
  end

  local function read_fx(tr)
    local fx = {}
    for i = 0, r.TrackFX_GetCount(tr) - 1 do
      local _, name = r.TrackFX_GetFXName(tr, i, "")
      fx[#fx + 1] = {
        guid = r.TrackFX_GetFXGUID(tr, i),
        name = name,
        enabled = r.TrackFX_GetEnabled(tr, i),
        offline = r.TrackFX_GetOffline(tr, i),
        param_count = r.TrackFX_GetNumParams(tr, i),
      }
    end
    return fx
  end

  local function env_flag(env, name, default)
    local ok, value = r.GetSetEnvelopeInfo_String(env, name, "", false)
    if not ok then return default end
    return value == "1" or value == "true"
  end

  local function read_envelopes(tr, fx_list)
    local envs = {}
    for i = 0, r.CountTrackEnvelopes(tr) - 1 do
      local env = r.GetTrackEnvelope(tr, i)
      local _, name = r.GetEnvelopeName(env)
      local info = {
        kind = "track",
        name = name,
        active = env_flag(env, "ACTIVE", true),
        visible = env_flag(env, "VISIBLE", false),
        point_count = r.CountEnvelopePointsEx(env, -1),
      }
      local _, fx_index, param_index = r.Envelope_GetParentTrack(env)
      if fx_index and fx_index >= 0 and param_index and param_index >= 0 and fx_list[fx_index + 1] then
        info.kind = "fx"
        info.fx = fx_list[fx_index + 1].guid
        info.param = U.int(param_index)
      end
      envs[#envs + 1] = info
    end
    return envs
  end

  local function read_tracks()
    local tracks = {}
    for i = 0, r.CountTracks(0) - 1 do
      local tr = r.GetTrack(0, i)
      local _, name = r.GetSetMediaTrackInfo_String(tr, "P_NAME", "", false)
      local parent = r.GetParentTrack(tr)
      local lane_mode = LANE_MODES[U.int(r.GetMediaTrackInfo_Value(tr, "I_FREEMODE"))] or "normal"
      local lane_names = {}
      if lane_mode == "fixed_lanes" then
        for lane = 0, U.int(r.GetMediaTrackInfo_Value(tr, "I_NUMFIXEDLANES")) - 1 do
          local _, lane_name = r.GetSetMediaTrackInfo_String(tr, "P_LANENAME:" .. lane, "", false)
          lane_names[#lane_names + 1] = lane_name or ""
        end
      end
      local fx = read_fx(tr)
      tracks[#tracks + 1] = {
        guid = track_guid(tr),
        name = name,
        color = U.hex_from_native(r.GetTrackColor(tr)),
        folder_depth = U.int(r.GetMediaTrackInfo_Value(tr, "I_FOLDERDEPTH")),
        parent = parent and track_guid(parent) or nil,
        lane_mode = lane_mode,
        lane_names = lane_names,
        automation_mode = AUTOMATION_MODES[r.GetTrackAutomationMode(tr)] or "trim_read",
        master_send = r.GetMediaTrackInfo_Value(tr, "B_MAINSEND") ~= 0,
        sends = read_sends(tr),
        fx = fx,
        envelopes = read_envelopes(tr, fx),
        agent_ext = U.ext_for_snapshot("track", tr),
      }
    end
    return tracks
  end

  local function read_items()
    local items = {}
    for i = 0, r.CountMediaItems(0) - 1 do
      local item = r.GetMediaItem(0, i)
      local active_index = U.int(r.GetMediaItemInfo_Value(item, "I_CURTAKE"))
      local takes = {}
      for t = 0, r.CountTakes(item) - 1 do
        local take = r.GetTake(item, t)
        local _, name = r.GetSetMediaItemTakeInfo_String(take, "P_NAME", "", false)
        local source = r.GetMediaItemTake_Source(take)
        local path = source and r.GetMediaSourceFileName(source, "") or ""
        takes[#takes + 1] = {
          guid = U.take_guid(take),
          name = name,
          source = path,
          offline = path ~= "" and not r.file_exists(path),
          active = (t == active_index),
          agent_ext = U.ext_for_snapshot("take", take),
        }
      end
      items[#items + 1] = {
        guid = U.item_guid(item),
        track = track_guid(r.GetMediaItemTrack(item)),
        position = U.round(r.GetMediaItemInfo_Value(item, "D_POSITION")),
        length = U.round(r.GetMediaItemInfo_Value(item, "D_LENGTH")),
        lane = U.int(r.GetMediaItemInfo_Value(item, "I_FIXEDLANE")),
        takes = takes,
        agent_ext = U.ext_for_snapshot("item", item),
      }
    end
    return items
  end

  local function read_markers()
    local markers = {}
    local i = 0
    while true do
      local ok, is_region, position, region_end, name, number, color = r.EnumProjectMarkers3(0, i)
      if not ok or ok == 0 then break end
      local _, guid = r.GetSetProjectInfo_String(0, "MARKER_GUID:" .. i, "", false)
      markers[#markers + 1] = {
        guid = guid,
        number = U.int(number),
        name = name,
        position = U.round(position),
        end_ = is_region and U.round(region_end) or nil,
        color = U.hex_from_native(color),
      }
      i = i + 1
    end
    return markers
  end

  local function read_tempo_map()
    local points = {}
    local count = r.CountTempoTimeSigMarkers(0)
    local first_time = nil
    if count > 0 then
      local _, t0 = r.GetTempoTimeSigMarker(0, 0)
      first_time = t0
    end
    if count == 0 or first_time > 0.000001 then
      local num, den, bpm = r.TimeMap_GetTimeSigAtTime(0, 0)
      points[#points + 1] = {
        time = 0.0, bpm = U.round(bpm, 4), num = U.int(num), den = U.int(den), linear = false,
      }
    end
    for i = 0, count - 1 do
      local _, time, _, _, bpm, num, den, linear = r.GetTempoTimeSigMarker(0, i)
      local at_num, at_den = r.TimeMap_GetTimeSigAtTime(0, time)
      points[#points + 1] = {
        time = U.round(time),
        bpm = U.round(bpm, 4),
        num = U.int(num > 0 and num or at_num),
        den = U.int(den > 0 and den or at_den),
        linear = linear and true or false,
      }
    end
    return points
  end

  -- REAPER upper-cases project ext-state keys when enumerating them (EnumProjExtState), so map
  -- them back to the canonical form: lower case, except the GUID in orig_region:<guid>.
  local function canonical_project_ext()
    local out = {}
    for key, value in pairs(U.project_ext_for_snapshot()) do
      local name = key:sub(#U.EXT_PREFIX + 1):lower()
      local guid = name:match("^orig_region:(%b{})$")
      if guid then name = "orig_region:" .. guid:upper() end
      out[U.EXT_PREFIX .. name] = value
    end
    return out
  end

  local function read_project()
    local _, path = r.EnumProjects(-1)
    return {
      name = r.GetProjectName(0),
      path = path or "",
      sample_rate = U.int(r.GetSetProjectInfo(0, "PROJECT_SRATE", 0, false)),
      reaper_version = r.GetAppVersion(),
      length = U.round(r.GetProjectLength(0)),
      agent_ext = canonical_project_ext(),
    }
  end

  return function()
    local snapshot = {
      schema_version = 1,
      project = read_project(),
      tempo_map = read_tempo_map(),
      tracks = read_tracks(),
      items = read_items(),
      markers = read_markers(),
    }
    -- "end" is a Lua keyword, so markers are built with end_; rename for the JSON shape.
    for _, marker in ipairs(snapshot.markers) do
      marker["end"] = marker.end_
      marker.end_ = nil
    end
    return snapshot
  end
end
