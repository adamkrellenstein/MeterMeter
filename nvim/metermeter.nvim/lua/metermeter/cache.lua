local config = require("metermeter.config")

local M = {}

function M.touch(st, entry)
  st.cache_seq = (tonumber(st.cache_seq) or 0) + 1
  entry.at = st.cache_seq
end

function M.get(st, key)
  local entry = st.cache[key]
  if not entry then
    return nil
  end
  M.touch(st, entry)
  return entry.payload
end

function M.put(st, key, payload)
  local entry = st.cache[key]
  if not entry then
    entry = { payload = payload, at = 0 }
    st.cache[key] = entry
    st.cache_size = (tonumber(st.cache_size) or 0) + 1
  else
    entry.payload = payload
  end
  M.touch(st, entry)
  st.cache_write_seq = (tonumber(st.cache_write_seq) or 0) + 1

  local max_entries = config.cache_max_entries()
  while (tonumber(st.cache_size) or 0) > max_entries do
    local oldest_key = nil
    local oldest_at = nil
    for k, v in pairs(st.cache) do
      local at = tonumber(v and v.at) or 0
      if oldest_at == nil or at < oldest_at then
        oldest_at = at
        oldest_key = k
      end
    end
    if oldest_key == nil then
      break
    end
    st.cache[oldest_key] = nil
    st.cache_size = math.max(0, (tonumber(st.cache_size) or 1) - 1)
  end
end

function M.key_for_text(st, text)
  local epoch = 0
  if st and st.cache_epoch then
    epoch = tonumber(st.cache_epoch) or 0
  end
  local ctx_meter = ""
  local ctx_strength = 0
  if st then
    ctx_meter = tostring(st.analysis_context_meter or "")
    ctx_strength = tonumber(st.analysis_context_strength) or 0
  end

  local ctx = ""
  if ctx_meter ~= "" and ctx_strength > 0 then
    ctx_strength = math.max(0, math.min(1, ctx_strength))
    ctx = ctx_meter .. "@" .. string.format("%.2f", ctx_strength)
  end

  return tostring(epoch) .. "\n" .. ctx .. "\n" .. text
end

return M
