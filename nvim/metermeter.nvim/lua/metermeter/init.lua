local uv = vim.uv or vim.loop

local M = {}

local ns = vim.api.nvim_create_namespace("metermeter")

local DEFAULTS = {
  enabled_by_default = true,

  max_line_length = 220,
  debounce_ms = 80,
  rescan_interval_ms = 1000,
  prefetch_lines = 80, -- also scan around cursor, not only visible lines

  highlight_stress = true,
  stress_style = "bold", -- "bold" | "bg"
  show_eol = true,
  eol_confidence_levels = 6, -- number of discrete tints for EOL meter annotation (>= 2)

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

  -- Executable: { "python3", "<plugin_root>/python/metermeter_cli.py" }
  cli_cmd = nil,

  debug_dump_path = "/tmp/metermeter_nvim_dump.json",
}

local cfg = vim.deepcopy(DEFAULTS)

local state_by_buf = {}

local function plugin_root()
  local src = debug.getinfo(1, "S").source
  local path = src:sub(2) -- drop leading "@"
  return vim.fn.fnamemodify(path, ":p:h:h:h") -- .../lua/metermeter/init.lua -> plugin root
end

local function buf_path(bufnr)
  local name = vim.api.nvim_buf_get_name(bufnr)
  return name or ""
end

local function has_ft_token(bufnr, token)
  local ft = (vim.bo[bufnr] and vim.bo[bufnr].filetype) or ""
  if type(ft) ~= "string" or ft == "" then
    return false
  end
  token = tostring(token or "")
  if token == "" then
    return false
  end
  if ft == token then
    return true
  end
  for part in string.gmatch(ft, "[^%.]+") do
    if part == token then
      return true
    end
  end
  return false
end

local function should_enable(bufnr)
  if not vim.api.nvim_buf_is_valid(bufnr) then
    return false
  end
  if vim.api.nvim_buf_get_option(bufnr, "buftype") ~= "" then
    return false
  end

  -- Modeline-friendly: user can opt-in via filetype.
  -- Example modeline: `vim: set ft=typst.metermeter :`
  return has_ft_token(bufnr, "metermeter")
end

local function compute_stress_hl()
  if (cfg.stress_style or "bold") == "bg" then
    -- A subtle background distinct from Normal.
    local normal = vim.api.nvim_get_hl(0, { name = "Normal", link = false }) or {}
    local bg = normal.bg or normal.background
    if type(bg) ~= "number" then
      local dark = (vim.o.background or ""):lower() ~= "light"
      vim.api.nvim_set_hl(0, "MeterMeterStress", { ctermbg = dark and 236 or 252 })
      vim.api.nvim_set_hl(0, "MeterMeterEOL", { link = "Comment" })
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
    local luma = (r * 0.2126) + (g * 0.7152) + (b * 0.0722)
    local delta = 22
    if luma < 128 then
      r = clamp(r + delta)
      g = clamp(g + delta)
      b = clamp(b + delta)
    else
      r = clamp(r - delta)
      g = clamp(g - delta)
      b = clamp(b - delta)
    end
    local new_bg = r * 65536 + g * 256 + b
    vim.api.nvim_set_hl(0, "MeterMeterStress", { bg = new_bg })
  else
    -- Bold overlay so it stays visible even when Normal bg is pure black.
    vim.api.nvim_set_hl(0, "MeterMeterStress", { bold = true })
  end
  vim.api.nvim_set_hl(0, "MeterMeterEOL", { link = "Comment" })
end

local function _blend_rgb(a, b, t)
  t = math.max(0, math.min(1, tonumber(t) or 0))
  local ar = math.floor(a / 65536) % 256
  local ag = math.floor(a / 256) % 256
  local ab = a % 256
  local br = math.floor(b / 65536) % 256
  local bg = math.floor(b / 256) % 256
  local bb = b % 256
  local rr = math.floor(ar + (br - ar) * t + 0.5)
  local rg = math.floor(ag + (bg - ag) * t + 0.5)
  local rb = math.floor(ab + (bb - ab) * t + 0.5)
  return rr * 65536 + rg * 256 + rb
end

local function compute_eol_hls()
  -- Confidence-driven meter hint: more confident => closer to Normal, less confident => closer to Comment.
  local levels = tonumber(cfg.eol_confidence_levels) or 6
  if levels < 2 then
    levels = 2
  end
  if levels > 12 then
    levels = 12
  end

  local normal = vim.api.nvim_get_hl(0, { name = "Normal", link = true }) or {}
  local comment = vim.api.nvim_get_hl(0, { name = "Comment", link = true }) or {}
  local nfg = normal.fg or normal.foreground
  local nbg = normal.bg or normal.background
  local cfgc = comment.fg or comment.foreground

  -- If we don't have truecolor info, fall back to cterm greys.
  if type(nfg) ~= "number" or type(nbg) ~= "number" or type(cfgc) ~= "number" then
    local dark = (vim.o.background or ""):lower() ~= "light"
    for i = 0, levels - 1 do
      local t = i / (levels - 1)
      -- On dark themes, higher t => brighter; on light themes, higher t => darker.
      local c = dark and (240 + math.floor(t * 14 + 0.5)) or (252 - math.floor(t * 14 + 0.5))
      vim.api.nvim_set_hl(0, "MeterMeterEOL" .. tostring(i), { ctermfg = c })
    end
    return
  end

  for i = 0, levels - 1 do
    local t = i / (levels - 1)
    local fg = _blend_rgb(cfgc, nfg, t)
    vim.api.nvim_set_hl(0, "MeterMeterEOL" .. tostring(i), { fg = fg })
  end
end

local function eol_hl_for_conf(conf)
  local levels = tonumber(cfg.eol_confidence_levels) or 6
  if levels < 2 then
    levels = 2
  end
  if levels > 12 then
    levels = 12
  end
  if type(conf) ~= "number" then
    return "MeterMeterEOL0"
  end
  conf = math.max(0, math.min(1, conf))
  local idx = math.floor(conf * (levels - 1) + 0.5)
  return "MeterMeterEOL" .. tostring(idx)
end

local function is_poetry_line(text)
  -- KISS: only annotate lines explicitly marked by a trailing backslash.
  local s = vim.trim(text or "")
  return s ~= "" and s:match("\\$") ~= nil
end

local function candidate_line_set_for_buf(bufnr)
  local wins = vim.fn.win_findbuf(bufnr)
  local out = {}
  local prefetch = tonumber(cfg.prefetch_lines) or 0
  local line_count = vim.api.nvim_buf_line_count(bufnr)
  local max_row = math.max(0, line_count - 1)
  for _, win in ipairs(wins) do
    win = tonumber(win) or win
    if type(win) == "number" and vim.api.nvim_win_is_valid(win) then
      local w0, w1 = vim.api.nvim_win_call(win, function()
        -- vim.fn.line() can return a string in some builds; coerce later.
        return vim.fn.line("w0"), vim.fn.line("w$")
      end)
      w0 = (tonumber(w0) or 1) - 1
      w1 = (tonumber(w1) or (w0 + 1)) - 1
      if w1 < w0 then
        w0, w1 = w1, w0
      end
      for l = w0, w1 do
        out[l] = true
      end

      if prefetch > 0 then
        local cur = vim.api.nvim_win_get_cursor(win)
        local row = (cur and cur[1] and tonumber(cur[1]) or 1) - 1
        local a = math.max(0, row - prefetch)
        local b = math.min(max_row, row + prefetch)
        for l = a, b do
          out[l] = true
        end
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
  local script = root .. "/python/metermeter_cli.py"
  return { "python3", script }
end

local function apply_results(bufnr, results)
  clear_buf(bufnr)
  if not results then
    return
  end
  -- Align meter hints at a consistent column (left-aligned), based on the widest annotated line.
  local max_w = 0
  for _, item in ipairs(results) do
    if type(item) == "table" and type(item.text) == "string" then
      local w = vim.fn.strdisplaywidth(item.text)
      if type(w) == "number" and w > max_w then
        max_w = w
      end
    end
  end
  local eol_col = max_w + 1
  local wins = vim.fn.win_findbuf(bufnr)
  if type(wins) == "table" then
    for _, win in ipairs(wins) do
      win = tonumber(win) or win
      if type(win) == "number" and vim.api.nvim_win_is_valid(win) then
        local ww = vim.api.nvim_win_get_width(win)
        if type(ww) == "number" and ww > 1 then
          -- Clamp to avoid placing completely offscreen.
          eol_col = math.min(eol_col, ww - 1)
        end
        break
      end
    end
  end

  for _, item in ipairs(results) do
    local lnum = tonumber(item.lnum)
    if lnum and vim.api.nvim_buf_is_valid(bufnr) then
      local label = item.label or ""
      local hint = item.hint or ""
      local src = item.source or ""
      local conf = item.confidence
      if type(conf) == "number" then
        label = tostring(item.meter_name or label or "")
      end
      if cfg.llm.hide_non_refined and src ~= "llm" then
        label = ""
        hint = ""
      end
      if cfg.show_eol and label ~= "" then
        local hl = eol_hl_for_conf(conf)
        vim.api.nvim_buf_set_extmark(bufnr, ns, lnum, 0, {
          virt_text = { { " " .. label, hl } },
          -- Left-aligned, but starting in a consistent column across all annotated lines.
          virt_text_pos = "overlay",
          virt_text_win_col = eol_col,
        })
      end
      if cfg.highlight_stress and type(item.stress_spans) == "table" then
        for _, span in ipairs(item.stress_spans) do
          local s = tonumber(span[1])
          local e = tonumber(span[2])
          if s and e and e > s then
            vim.api.nvim_buf_set_extmark(bufnr, ns, lnum, s, {
              end_col = e,
              hl_group = "MeterMeterStress",
              hl_mode = "combine",
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
  local max_len = tonumber(cfg.max_line_length) or 220

  local line_count = vim.api.nvim_buf_line_count(bufnr)

  local lines = {}
  local st = ensure_state(bufnr)

  for lnum, _ in pairs(line_set) do
    if lnum >= 0 and lnum < line_count then
      local text = (vim.api.nvim_buf_get_lines(bufnr, lnum, lnum + 1, false)[1] or "")
      local ok = true
      if ok and not is_poetry_line(text) then
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
  local line_set = candidate_line_set_for_buf(bufnr)
  local line_count = vim.api.nvim_buf_line_count(bufnr)
  for lnum, _ in pairs(line_set) do
    if lnum >= 0 and lnum < line_count then
      local text = (vim.api.nvim_buf_get_lines(bufnr, lnum, lnum + 1, false)[1] or "")
      if is_poetry_line(text) then
        local key = cfg.llm.model .. "\n" .. tostring(cfg.llm.endpoint or "") .. "\n" .. text
        local cached = st.cache[key]
        if cached then
          table.insert(out, cached)
        end
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
    -- vim.system callbacks run in a "fast event" context; bounce back to main loop.
    vim.schedule(function()
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

  local line_set = candidate_line_set_for_buf(bufnr)
  local req = build_request(bufnr, line_set)
  if #req.lines == 0 then
    -- Still render cached lines (e.g. after colorscheme change).
    local results = merge_cache_and_results(bufnr, { results = {} })
    apply_results(bufnr, results)
    return
  end

  run_cli(req, function(resp, err)
    if err then
      -- Keep stale annotations; user can :MeterMeterDump to inspect.
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
  local path = cfg.debug_dump_path or "/tmp/metermeter_nvim_dump.json"
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
  compute_eol_hls()
  vim.api.nvim_create_autocmd("ColorScheme", {
    group = vim.api.nvim_create_augroup("MeterMeterColors", { clear = true }),
    callback = function()
      compute_stress_hl()
      compute_eol_hls()
    end,
  })

  local group = vim.api.nvim_create_augroup("MeterMeter", { clear = true })
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
