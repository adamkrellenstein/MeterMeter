local M = {}

local DEFAULTS = {
  debounce_ms = 80,
  rescan_interval_ms = 0,
  prefetch_lines = 5, -- also scan around cursor, not only visible lines

  ui = {
    stress = true,
    meter_hints = true,
    meter_hint_abbrev = true,
    -- "off" | "deviations" | "always"
    -- - off: show only meter_name
    -- - deviations: show only non-default notes (e.g. feminine ending, inversions, spondees)
    -- - always: always show ending (masc/fem) plus any substitutions
    meter_hint_details = "deviations",
    confident_threshold = 0.7, -- >= this: bright (MeterMeterEOL1)
    -- below confident_threshold => dim guess (MeterMeterEOL0)
    loading_indicator = true,
  },

  -- If true, only annotate lines that end with a trailing "\" (useful for mixed-format files).
  -- If false (default), annotate every non-comment line.
  require_trailing_backslash = false,

  cache = {
    max_entries = 5000,
  },

  debug_dump_path = "/tmp/metermeter_nvim_dump.json",
}

M.cfg = vim.deepcopy(DEFAULTS)

local function _to_bool(v)
  if v == nil then
    return nil
  end
  if v == true or v == 1 or v == "1" then
    return true
  end
  if v == false or v == 0 or v == "0" then
    return false
  end
  if type(v) == "string" then
    local s = v:lower()
    if s == "true" or s == "yes" or s == "on" then
      return true
    end
    if s == "false" or s == "no" or s == "off" then
      return false
    end
  end
  return nil
end

local function _to_number(v)
  if v == nil then
    return nil
  end
  local n = tonumber(v)
  return n
end

local function _to_string(v)
  if v == nil then
    return nil
  end
  if type(v) == "string" then
    return v
  end
  return tostring(v)
end

local function _apply_vim_globals(cfg)
  -- Global config for init.vim/init.lua (read during setup()).
  -- Vimscript example:
  --   let g:metermeter_debounce_ms = 120
  --   let g:metermeter_ui_meter_hint_abbrev = v:true
  local g = vim.g
  local n

  n = _to_number(g.metermeter_debounce_ms)
  if n then
    cfg.debounce_ms = n
  end
  n = _to_number(g.metermeter_rescan_interval_ms)
  if n then
    cfg.rescan_interval_ms = n
  end
  n = _to_number(g.metermeter_prefetch_lines)
  if n then
    cfg.prefetch_lines = n
  end
  local b = _to_bool(g.metermeter_require_trailing_backslash)
  if b ~= nil then
    cfg.require_trailing_backslash = b
  end

  cfg.ui = cfg.ui or {}
  b = _to_bool(g.metermeter_ui_stress)
  if b ~= nil then
    cfg.ui.stress = b
  end
  b = _to_bool(g.metermeter_ui_meter_hints)
  if b ~= nil then
    cfg.ui.meter_hints = b
  end
  b = _to_bool(g.metermeter_ui_meter_hint_abbrev)
  if b ~= nil then
    cfg.ui.meter_hint_abbrev = b
  end
  local s = _to_string(g.metermeter_ui_meter_hint_details)
  if type(s) == "string" then
    s = s:lower()
    if s == "off" or s == "always" or s == "deviations" then
      cfg.ui.meter_hint_details = s
    end
  end
  n = _to_number(g.metermeter_ui_confident_threshold)
  if n then
    cfg.ui.confident_threshold = n
  end
  b = _to_bool(g.metermeter_ui_loading_indicator)
  if b ~= nil then
    cfg.ui.loading_indicator = b
  end

  cfg.cache = cfg.cache or {}
  n = _to_number(g.metermeter_cache_max_entries)
  if n then
    cfg.cache.max_entries = n
  end
  s = _to_string(g.metermeter_debug_dump_path)
  if type(s) == "string" and s ~= "" then
    cfg.debug_dump_path = s
  end

  return cfg
end

---@param opts? table
function M.apply(opts)
  local cfg = vim.tbl_deep_extend("force", vim.deepcopy(DEFAULTS), opts or {})
  cfg = _apply_vim_globals(cfg)
  M.cfg = cfg
end

function M.cache_max_entries()
  local n = tonumber(M.cfg.cache and M.cfg.cache.max_entries) or 5000
  if n < 100 then
    n = 100
  end
  return math.floor(n)
end

function M.plugin_root()
  local src = debug.getinfo(1, "S").source
  local path = src:sub(2) -- drop leading "@"
  path = vim.fn.resolve(path) -- resolve to absolute path
  return vim.fn.fnamemodify(path, ":p:h:h:h") -- .../lua/metermeter/config.lua -> plugin root
end

return M
