-- agent.apply_plan: run a plan's ops in order inside ONE named undo block.
-- Loaded by handlers.lua; returns function(ctx, U, registry) -> handler(command).
--
-- payload = { plan = { id, summary, ops = [ {type=..., ...}, ... ] } }
-- reply   = { ok, plan_id, results = [ {index, type, ok, error?, data?} ], refs, rolled_back, error? }
-- On the first failing op the run stops, the block is closed and, if the block is REAPER's most
-- recent undo point, it is undone (rolled_back = true). dry_run is handled in Python (simulation);
-- here it only guards against being called by mistake.

return function(ctx, U, registry)
  local r = ctx.reaper

  local function new_state()
    local S = { refs = {}, tracks = {}, items = {}, takes = {} }

    local function key(guid)
      return guid:upper()
    end

    function S.scan()
      for i = 0, r.CountTracks(0) - 1 do
        local tr = r.GetTrack(0, i)
        S.tracks[key(r.GetTrackGUID(tr))] = tr
      end
      for i = 0, r.CountMediaItems(0) - 1 do
        local item = r.GetMediaItem(0, i)
        S.items[key(U.item_guid(item))] = item
        for t = 0, r.CountTakes(item) - 1 do
          local take = r.GetTake(item, t)
          S.takes[key(U.take_guid(take))] = take
        end
      end
    end

    function S.resolve(id)
      if type(id) ~= "string" then error("target is not a string") end
      if id:sub(1, 4) == "ref:" then
        local guid = S.refs[id]
        if not guid then error("unresolved reference " .. id) end
        return guid
      end
      return id
    end

    function S.track(id)
      local tr = S.tracks[key(S.resolve(id))]
      if not tr then error("unknown track " .. tostring(id)) end
      return tr
    end

    function S.item(id)
      local item = S.items[key(S.resolve(id))]
      if not item then error("unknown item " .. tostring(id)) end
      return item
    end

    function S.take(id)
      local take = S.takes[key(S.resolve(id))]
      if not take then error("unknown take " .. tostring(id)) end
      return take
    end

    function S.add_track(tr)
      local guid = r.GetTrackGUID(tr)
      S.tracks[key(guid)] = tr
      return guid
    end

    function S.define_ref(ref, guid)
      S.refs[ref] = guid
    end

    -- Save the pre-change value once, under reaper_agent ext state, so a tidy stays reversible
    -- even after the project is saved. Never overwrites an existing original.
    function S.save_original(kind, obj, name, value)
      local blob = U.ext_read(kind, obj)
      if blob[name] == nil then
        U.ext_write(kind, obj, name, tostring(value))
      end
    end

    return S
  end

  local function run(command)
    local payload = command.payload or {}
    local plan = payload.plan
    if type(plan) ~= "table" or type(plan.ops) ~= "table" or #plan.ops == 0 then
      error("BAD_PLAN: payload.plan.ops must be a non-empty array")
    end
    local summary = tostring(plan.summary or "reaper-agent plan")
    local plan_id = tostring(plan.id or "")
    if command.dry_run or payload.dry_run then
      return { ok = true, plan_id = plan_id, results = {}, refs = {}, rolled_back = false, dry_run = true }
    end

    local S = new_state()
    S.scan()
    local selected = {}
    for i = 0, r.CountSelectedTracks(0) - 1 do
      selected[#selected + 1] = r.GetSelectedTrack(0, i)
    end

    local changes_before = r.GetProjectStateChangeCount(0)
    local results = {}
    local failure = nil

    r.Undo_BeginBlock2(0)
    r.PreventUIRefresh(1)
    local loop_ok, loop_err = pcall(function()
      for i, op in ipairs(plan.ops) do
        local entry = { index = i - 1, type = tostring(op.type), ok = false }
        results[#results + 1] = entry
        local handler = registry[op.type]
        if not handler then
          entry.error = "no handler for op type " .. tostring(op.type)
          failure = "op " .. (i - 1) .. " (" .. entry.type .. "): " .. entry.error
          return
        end
        local ok, data = pcall(handler, op, S)
        if ok then
          entry.ok = true
          entry.data = type(data) == "table" and data or nil
        else
          entry.error = tostring(data):gsub("^.-:%d+: ", "")
          failure = "op " .. (i - 1) .. " (" .. entry.type .. "): " .. entry.error
          return
        end
      end
    end)
    if not loop_ok then
      failure = failure or ("unexpected error: " .. tostring(loop_err))
    end
    -- Moving tracks changes the selection; put it back.
    r.Main_OnCommand(40297, 0) -- unselect all tracks
    for _, tr in ipairs(selected) do
      if r.ValidatePtr2(0, tr, "MediaTrack*") then r.SetTrackSelected(tr, true) end
    end
    r.PreventUIRefresh(-1)
    r.TrackList_AdjustWindows(false)
    r.UpdateArrange()
    r.Undo_EndBlock2(0, summary, -1)

    local rolled_back = false
    if failure then
      -- Undo our own block, but only if it really is the newest undo point (a failure before
      -- any change leaves no point, and undoing then would revert the user's earlier edit).
      local top = r.Undo_CanUndo2(0)
      if top == summary and r.GetProjectStateChangeCount(0) ~= changes_before then
        r.Undo_DoUndo2(0)
        rolled_back = true
      end
      return {
        ok = false, plan_id = plan_id, results = results, refs = {},
        rolled_back = rolled_back, error = failure,
      }
    end
    return { ok = true, plan_id = plan_id, results = results, refs = S.refs, rolled_back = false }
  end

  return run
end
