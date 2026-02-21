local uv = vim.uv or vim.loop

local M = {}

local ns = vim.api.nvim_create_namespace("poetrymeter")

local DEFAULTS = {
  enabled_by_default = true,
  enabled_file_extensions = { ".poem" },
  opt_in_file_extensions = { ".typ" },
  opt_in_marker = "poetrymeter: on",
  opt_out_marker = "poetrymeter: off",
  marker_scan_max_lines = 25,

  max_line_length = 220,
  debounce_ms = 80,
  rescan_interval_ms = 1000,

  highlight_stress = true,
  show_eol = true,

  -- LLM refinement (optional).
  llm = {
    enabled = true,
    endpoint = "http://127.0.0.1:11434/v1/chat/completions",
    model = "qwen2.5:7b-instruct",
    timeout_ms = 30000,
    temperature = 0.1,
    max_lines_per_scan = 2,
    hide_non_refined = false,
  },

  -- Executable: { "python3", "<plugin_root>/python/poetrymeter_cli.py" }
  cli_cmd = nil,

  debug_dump_path = "/tmp/poetrymeter_nvim_dump.json",
}

local cfg = vim.deepcopy(DEFAULTS)

local state_by_buf = {}

local function plugin_root()
  local src = debug.getinfo(1, "S").source
  local path = src:sub(2) -- drop leading "@"
  return vim.fn.fnamemodify(path, ":p:h:h:h") -- .../lua/poetrymeter/init.lua -> plugin root
end

local function norm_exts(exts)
  local out = {}
  for _, ext in ipairs(exts or {}) do
    if type(ext) == "string" and ext ~= "" then
      if ext:sub(1, 1) ~= "." then
        ext = "." .. ext
      end
      out[ext:lower()] = true
    end
  end
  return out
end

local function buf_path(bufnr)
  local name = vim.api.nvim_buf_get_name(bufnr)
  return name or ""
end

local function buf_ext(bufnr)
  local name = buf_path(bufnr):lower()
  local dot = name:match("^.*()%.")
  if not dot then
    return ""
  end
  return name:sub(dot)
end

local function read_marker_state(bufnr)
  local max_lines = tonumber(cfg.marker_scan_max_lines) or 0
  if max_lines <= 0 then
    return nil
  end
  local opt_in = tostring(cfg.opt_in_marker or ""):lower()
  local opt_out = tostring(cfg.opt_out_marker or ""):lower()
  if opt_in == "" and opt_out == "" then
    return nil
  end
  local line_count = vim.api.nvim_buf_line_count(bufnr)
  local limit = math.min(line_count, max_lines)
  local lines = vim.api.nvim_buf_get_lines(bufnr, 0, limit, false)
  for _, line in ipairs(lines) do
    local low = vim.trim(line):lower()
    if opt_out ~= "" and low:find(opt_out, 1, true) then
      return false
    end
    if opt_in ~= "" and low:find(opt_in, 1, true) then
      return true
    end
  end
  return nil
end

local function should_enable(bufnr)
  if not vim.api.nvim_buf_is_valid(bufnr) then
    return false
  end
  if vim.api.nvim_buf_get_option(bufnr, "buftype") ~= "" then
    return false
  end

  local ext = buf_ext(bufnr)
  local enabled_exts = norm_exts(cfg.enabled_file_extensions)
  local opt_in_exts = norm_exts(cfg.opt_in_file_extensions)

  local marker = read_marker_state(bufnr)
  if marker == false then
    return false
  end

  if enabled_exts[ext] then
    return true
  end
  if opt_in_exts[ext] and marker == true then
    return true
  end

  return false
end

local function compute_stress_hl()
  -- A subtle background a bit darker than Normal.
  local normal = vim.api.nvim_get_hl(0, { name = "Normal", link = false }) or {}
  local bg = normal.bg or normal.background
  if type(bg) ~= "number" then
    -- Fallback: link to Visual, but most colorschemes define Normal bg anyway.
    vim.api.nvim_set_hl(0, "PoetryMeterStress", { link = "Visual" })
    vim.api.nvim_set_hl(0, "PoetryMeterEOL", { link = "Comment" })
    return
  end
  local r = math.floor(bg / 65536) % 256
  local g = math.floor(bg / 256) % 256
  local b = bg % 256
  local function clamp(x)
    if x < 0 then
      return 0
    end
    if x > 255 then
      return 255
    end
    return x
  end
  -- Darken slightly.
  r = clamp(r - 14)
  g = clamp(g - 14)
  b = clamp(b - 14)
  local new_bg = r * 65536 + g * 256 + b
  vim.api.nvim_set_hl(0, "PoetryMeterStress", { bg = new_bg })
  vim.api.nvim_set_hl(0, "PoetryMeterEOL", { link = "Comment" })
end

local function typst_allowed_lines_upto(lines, max_row)
  -- lines: 0-based Lua array (list) from 0..max_row inclusive (strings)
  local allowed = {}
  local in_block = false
  local depth = 0
  local starters = { "#stanza[", "#couplet[", "#poem[" }

  for row = 0, max_row do
    local raw = lines[row + 1] or ""
    local stripped = vim.trim(raw)
    local low = stripped:lower()

    if not in_block then
      for _, st in ipairs(starters) do
        if low:sub(1, #st) == st then
          in_block = true
          local delta = select(2, raw:gsub("%[", "")) - select(2, raw:gsub("%]", ""))
          depth = (delta > 0) and delta or 1
          break
        end
      end
    else
      local delta = select(2, raw:gsub("%[", "")) - select(2, raw:gsub("%]", ""))
      depth = depth + delta
      if depth <= 0 then
        in_block = false
        depth = 0
      else
        if stripped ~= "" and stripped:sub(1, 1) ~= "#" and stripped:sub(1, 1) ~= "]" then
          if stripped:sub(1, 1) ~= "{" and stripped:sub(1, 1) ~= "}" then
            allowed[row] = true
          end
        end
      end
    end
  end

  return allowed
end

local function visible_line_set_for_buf(bufnr)
  local wins = vim.fn.win_findbuf(bufnr)
  local out = {}
  for _, win in ipairs(wins) do
    if vim.api.nvim_win_is_valid(win) then
      local w0, w1 = vim.api.nvim_win_call(win, function()
        return vim.fn.line("w0") - 1, vim.fn.line("w$") - 1
      end)
      for l = w0, w1 do
        out[l] = true
      end
    end
  end
  return out
end

local function json_encode(x)
  return vim.json.encode(x)
end

local function json_decode(s)
  return vim.json.decode(s)
end

local function ensure_state(bufnr)
  local st = state_by_buf[bufnr]
  if st then
    return st
  end
  st = {
    enabled = false,
    timer = nil,
    tick = nil,
    cache = {},
    last_lines = {},
  }
  state_by_buf[bufnr] = st
  return st
end

local function clear_buf(bufnr)
  if not vim.api.nvim_buf_is_valid(bufnr) then
    return
  end
  vim.api.nvim_buf_clear_namespace(bufnr, ns, 0, -1)
end

local function cli_cmd()
  if cfg.cli_cmd then
    return cfg.cli_cmd
  end
  local root = plugin_root()
  local script = root .. "/python/poetrymeter_cli.py"
  return { "python3", script }
end

local function apply_results(bufnr, results)
  clear_buf(bufnr)
  if not results then
    return
  end
  for _, item in ipairs(results) do
    local lnum = tonumber(item.lnum)
    if lnum and vim.api.nvim_buf_is_valid(bufnr) then
      local label = item.label or ""
      local hint = item.hint or ""
      local src = item.source or ""
      local conf = item.confidence
      if type(conf) == "number" then
        label = string.format("%s %.0f%%", item.meter_name or label, conf * 100.0)
      end
      if cfg.llm.hide_non_refined and src ~= "llm" then
        label = ""
        hint = ""
      end
      if cfg.show_eol and label ~= "" then
        vim.api.nvim_buf_set_extmark(bufnr, ns, lnum, 0, {
          virt_text = { { " " .. label, "PoetryMeterEOL" } },
          virt_text_pos = "eol",
        })
      end
      if cfg.highlight_stress and type(item.stress_spans) == "table" then
        for _, span in ipairs(item.stress_spans) do
          local s = tonumber(span[1])
          local e = tonumber(span[2])
          if s and e and e > s then
            vim.api.nvim_buf_set_extmark(bufnr, ns, lnum, s, {
              end_col = e,
              hl_group = "PoetryMeterStress",
            })
          end
        end
      end
      -- Optional: stash hint for dump/debug.
      if hint ~= "" then
        -- Put it in the cache as well; we don't render it by default.
      end
    end
  end
end

local function build_request(bufnr, line_set)
  local ext = buf_ext(bufnr)
  local max_len = tonumber(cfg.max_line_length) or 220

  local line_count = vim.api.nvim_buf_line_count(bufnr)
  local max_row = line_count - 1

  local allowed_typst = nil
  if ext == ".typ" then
    local lines = vim.api.nvim_buf_get_lines(bufnr, 0, line_count, false)
    allowed_typst = typst_allowed_lines_upto(lines, max_row)
  end

  local lines = {}
  local st = ensure_state(bufnr)

  for lnum, _ in pairs(line_set) do
    if lnum >= 0 and lnum < line_count then
      local text = (vim.api.nvim_buf_get_lines(bufnr, lnum, lnum + 1, false)[1] or "")
      local ok = true
      if allowed_typst and not allowed_typst[lnum] then
        ok = false
      end
      if ok and (#text == 0 or #text > max_len) then
        ok = false
      end
      local key = cfg.llm.model .. "\n" .. tostring(cfg.llm.endpoint or "") .. "\n" .. text
      if ok and st.cache[key] then
        ok = false
      end
      if ok then
        table.insert(lines, { lnum = lnum, text = text })
      end
    end
  end

  return {
    config = {
      llm = cfg.llm,
    },
    lines = lines,
  }
end

local function merge_cache_and_results(bufnr, resp)
  local st = ensure_state(bufnr)
  if type(resp) ~= "table" or type(resp.results) ~= "table" then
    return {}
  end
  for _, item in ipairs(resp.results) do
    if type(item) == "table" and type(item.text) == "string" then
      local key = cfg.llm.model .. "\n" .. tostring(cfg.llm.endpoint or "") .. "\n" .. item.text
      st.cache[key] = item
    end
  end
  -- Return results for current visible lines from cache.
  local out = {}
  local line_set = visible_line_set_for_buf(bufnr)
  local line_count = vim.api.nvim_buf_line_count(bufnr)
  for lnum, _ in pairs(line_set) do
    if lnum >= 0 and lnum < line_count then
      local text = (vim.api.nvim_buf_get_lines(bufnr, lnum, lnum + 1, false)[1] or "")
      local key = cfg.llm.model .. "\n" .. tostring(cfg.llm.endpoint or "") .. "\n" .. text
      local cached = st.cache[key]
      if cached then
        table.insert(out, cached)
      end
    end
  end
  table.sort(out, function(a, b)
    return (a.lnum or 0) < (b.lnum or 0)
  end)
  return out
end

local function run_cli(input, cb)
  local cmd = cli_cmd()
  local text = json_encode(input)
  vim.system(cmd, { stdin = text, text = true }, function(res)
    if res.code ~= 0 then
      cb(nil, "cli exited " .. tostring(res.code) .. ": " .. (res.stderr or ""))
      return
    end
    local ok, obj = pcall(json_decode, res.stdout or "")
    if not ok then
      cb(nil, "bad json from cli: " .. tostring(obj))
      return
    end
    cb(obj, nil)
  end)
end

local function do_scan(bufnr)
  if not vim.api.nvim_buf_is_valid(bufnr) then
    return
  end
  local st = ensure_state(bufnr)
  if not st.enabled then
    return
  end
  if not should_enable(bufnr) then
    return
  end

  local line_set = visible_line_set_for_buf(bufnr)
  local req = build_request(bufnr, line_set)
  if #req.lines == 0 then
    -- Still render cached lines (e.g. after colorscheme change).
    local results = merge_cache_and_results(bufnr, { results = {} })
    apply_results(bufnr, results)
    return
  end

  run_cli(req, function(resp, err)
    if err then
      -- Keep stale annotations; user can :PoetryMeterDump to inspect.
      return
    end
    local results = merge_cache_and_results(bufnr, resp)
    apply_results(bufnr, results)
  end)
end

local function schedule_scan(bufnr)
  local st = ensure_state(bufnr)
  if st.timer then
    st.timer:stop()
    st.timer:close()
    st.timer = nil
  end
  st.timer = uv.new_timer()
  local ms = tonumber(cfg.debounce_ms) or 80
  st.timer:start(ms, 0, function()
    vim.schedule(function()
      do_scan(bufnr)
    end)
  end)
end

local function ensure_tick(bufnr)
  local st = ensure_state(bufnr)
  if st.tick then
    return
  end
  local ms = tonumber(cfg.rescan_interval_ms) or 0
  if ms <= 0 then
    return
  end
  st.tick = uv.new_timer()
  st.tick:start(ms, ms, function()
    vim.schedule(function()
      do_scan(bufnr)
    end)
  end)
end

function M.enable(bufnr)
  bufnr = (bufnr == 0) and vim.api.nvim_get_current_buf() or bufnr
  local st = ensure_state(bufnr)
  st.enabled = true
  ensure_tick(bufnr)
  schedule_scan(bufnr)
end

function M.disable(bufnr)
  bufnr = (bufnr == 0) and vim.api.nvim_get_current_buf() or bufnr
  local st = ensure_state(bufnr)
  st.enabled = false
  if st.timer then
    st.timer:stop()
    st.timer:close()
    st.timer = nil
  end
  if st.tick then
    st.tick:stop()
    st.tick:close()
    st.tick = nil
  end
  clear_buf(bufnr)
end

function M.toggle(bufnr)
  bufnr = (bufnr == 0) and vim.api.nvim_get_current_buf() or bufnr
  local st = ensure_state(bufnr)
  if st.enabled then
    M.disable(bufnr)
  else
    M.enable(bufnr)
  end
end

function M.rescan(bufnr)
  bufnr = (bufnr == 0) and vim.api.nvim_get_current_buf() or bufnr
  schedule_scan(bufnr)
end

function M.dump_debug(bufnr)
  bufnr = (bufnr == 0) and vim.api.nvim_get_current_buf() or bufnr
  local st = ensure_state(bufnr)
  local marks = vim.api.nvim_buf_get_extmarks(bufnr, ns, 0, -1, { details = true })
  local out = {
    ts = os.time(),
    bufnr = bufnr,
    file = buf_path(bufnr),
    enabled = st.enabled,
    extmarks = marks,
  }
  local path = cfg.debug_dump_path or "/tmp/poetrymeter_nvim_dump.json"
  local ok, enc = pcall(json_encode, out)
  if ok then
    local f = io.open(path, "w")
    if f then
      f:write(enc)
      f:close()
    end
  end
end

function M.setup(opts)
  opts = opts or {}
  cfg = vim.tbl_deep_extend("force", vim.deepcopy(DEFAULTS), opts)

  compute_stress_hl()
  vim.api.nvim_create_autocmd("ColorScheme", {
    group = vim.api.nvim_create_augroup("PoetryMeterColors", { clear = true }),
    callback = compute_stress_hl,
  })

  local group = vim.api.nvim_create_augroup("PoetryMeter", { clear = true })
  vim.api.nvim_create_autocmd({ "BufReadPost", "BufNewFile", "BufEnter" }, {
    group = group,
    callback = function(args)
      if should_enable(args.buf) and cfg.enabled_by_default then
        M.enable(args.buf)
      end
    end,
  })
  vim.api.nvim_create_autocmd({ "TextChanged", "TextChangedI", "WinScrolled", "CursorHold" }, {
    group = group,
    callback = function(args)
      local st = ensure_state(args.buf)
      if st.enabled then
        schedule_scan(args.buf)
      end
    end,
  })
end

return M
