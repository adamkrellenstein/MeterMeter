local M = {}

local DEFAULTS = {
  debounce_ms = 80,
  rescan_interval_ms = 0,
  prefetch_lines = 5, -- also scan around cursor, not only visible lines

  ui = {
    stress = true,
    meter_hints = true,
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

---@param opts? table
function M.apply(opts)
  M.cfg = vim.tbl_deep_extend("force", vim.deepcopy(DEFAULTS), opts or {})
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
