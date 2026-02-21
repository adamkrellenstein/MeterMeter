local uv = vim.uv or vim.loop

local M = {}

local ns = vim.api.nvim_create_namespace("metermeter")

local DEFAULTS = {
  debounce_ms = 80,
  rescan_interval_ms = 1000,
  prefetch_lines = 5, -- also scan around cursor, not only visible lines

  ui = {
    stress = true,
    meter_hints = true,
    meter_hint_confidence_levels = 6, -- number of discrete tint steps (>= 2)
  },

  -- If true, only annotate lines that end with a trailing "\" (useful for mixed-format files).
  -- If false (default), annotate every non-comment line.
  require_trailing_backslash = false,

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

  debug_dump_path = "/tmp/metermeter_nvim_dump.json",
}

local cfg = vim.deepcopy(DEFAULTS)

local state_by_buf = {}
local ENGINE_VISIBLE_CHUNK = 12
local ENGINE_PREFETCH_CHUNK = 20
local run_cli

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
  -- Bold overlay so it stays visible even when Normal bg is pure black.
  vim.api.nvim_set_hl(0, "MeterMeterStress", { bold = true })
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
  local levels = tonumber(cfg.ui.meter_hint_confidence_levels) or 6
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
  local levels = tonumber(cfg.ui.meter_hint_confidence_levels) or 6
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

local function _split_csv(s)
  if type(s) ~= "string" or s == "" then
    return {}
  end
  local out = {}
  for part in string.gmatch(s, "([^,]+)") do
    table.insert(out, vim.trim(part))
  end
  return out
end

local function _comment_leaders(bufnr)
  local leaders = {}
  local seen = {}

  local comments = (vim.bo[bufnr] and vim.bo[bufnr].comments) or ""
  for _, entry in ipairs(_split_csv(comments)) do
    if entry ~= "" then
      local leader = entry
      local colon = string.find(entry, ":", 1, true)
      if colon then
        leader = entry:sub(colon + 1)
      end
      leader = leader or ""
      leader = leader:gsub("\\,", ",") -- best-effort
      leader = leader:gsub("\\\\", "\\")
      leader = vim.trim(leader)
      if leader ~= "" and not seen[leader] then
        seen[leader] = true
        table.insert(leaders, leader)
      end
    end
  end

  local cs = (vim.bo[bufnr] and vim.bo[bufnr].commentstring) or ""
  if type(cs) == "string" and cs:find("%%s", 1, true) then
    local prefix = cs:match("^(.-)%%s")
    if prefix then
      prefix = vim.trim(prefix)
      if prefix ~= "" and not seen[prefix] then
        seen[prefix] = true
        table.insert(leaders, prefix)
      end
    end
  end

  -- Prefer longer leaders first (e.g. "///" before "//").
  table.sort(leaders, function(a, b)
    return #a > #b
  end)

  return leaders
end

local function is_comment_line(bufnr, text)
  local s = text or ""
  s = s:gsub("^%s+", "")
  if s == "" then
    return false
  end
  for _, leader in ipairs(_comment_leaders(bufnr)) do
    if leader ~= "" then
      local l = leader
      local l2 = leader:gsub("%s+$", "")
      if s:sub(1, #l) == l or (l2 ~= "" and s:sub(1, #l2) == l2) then
        return true
      end
    end
  end
  return false
end

local function is_scan_line(bufnr, text)
  local s = vim.trim(text or "")
  if s == "" then
    return false
  end
  if is_comment_line(bufnr, s) then
    return false
  end
  if cfg.require_trailing_backslash then
    return s:match("\\$") ~= nil
  end
  return true
end

local function candidate_line_set_for_buf(bufnr)
  local wins = vim.fn.win_findbuf(bufnr)
  local visible = {}
  local prefetch_set = {}
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
        visible[l] = true
      end

      if prefetch > 0 then
        local cur = vim.api.nvim_win_get_cursor(win)
        local row = (cur and cur[1] and tonumber(cur[1]) or 1) - 1
        local a = math.max(0, row - prefetch)
        local b = math.min(max_row, row + prefetch)
        for l = a, b do
          if not visible[l] then
            prefetch_set[l] = true
          end
        end
      end
    end
  end

  local visible_lines = {}
  for lnum, _ in pairs(visible) do
    table.insert(visible_lines, lnum)
  end
  table.sort(visible_lines)

  local prefetch_lines = {}
  for lnum, _ in pairs(prefetch_set) do
    table.insert(prefetch_lines, lnum)
  end
  table.sort(prefetch_lines)

  return visible_lines, prefetch_lines
end

local function combine_lines(visible_lines, prefetch_lines)
  local out = {}
  local seen = {}
  for _, lnum in ipairs(visible_lines or {}) do
    if not seen[lnum] then
      seen[lnum] = true
      table.insert(out, lnum)
    end
  end
  for _, lnum in ipairs(prefetch_lines or {}) do
    if not seen[lnum] then
      seen[lnum] = true
      table.insert(out, lnum)
    end
  end
  return out
end

local function all_scan_lines_for_buf(bufnr)
  local out = {}
  local line_count = vim.api.nvim_buf_line_count(bufnr)
  for lnum = 0, line_count - 1 do
    local text = (vim.api.nvim_buf_get_lines(bufnr, lnum, lnum + 1, false)[1] or "")
    if is_scan_line(bufnr, text) then
      table.insert(out, lnum)
    end
  end
  return out
end

local function subtract_lines(base_lines, remove_lines)
  local seen = {}
  for _, lnum in ipairs(remove_lines or {}) do
    seen[lnum] = true
  end
  local out = {}
  for _, lnum in ipairs(base_lines or {}) do
    if not seen[lnum] then
      table.insert(out, lnum)
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
    scan_generation = 0,
    scan_running = false,
    scan_changedtick = -1,
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

local function default_cli_cmd()
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
      if cfg.ui.meter_hints and label ~= "" then
        local hl = eol_hl_for_conf(conf)
        vim.api.nvim_buf_set_extmark(bufnr, ns, lnum, 0, {
          virt_text = { { " " .. label, hl } },
          -- Left-aligned, but starting in a consistent column across all annotated lines.
          virt_text_pos = "overlay",
          virt_text_win_col = eol_col,
        })
      end
      if cfg.ui.stress and type(item.stress_spans) == "table" then
        local line_count = vim.api.nvim_buf_line_count(bufnr)
        if lnum >= 0 and lnum < line_count then
          local line_text = (vim.api.nvim_buf_get_lines(bufnr, lnum, lnum + 1, false)[1] or "")
          local line_bytes = #line_text
          for _, span in ipairs(item.stress_spans) do
            local s = tonumber(span[1])
            local e = tonumber(span[2])
            if s and e then
              s = math.max(0, math.min(line_bytes, s))
              e = math.max(0, math.min(line_bytes, e))
            end
            if s and e and e > s then
              vim.api.nvim_buf_set_extmark(bufnr, ns, lnum, s, {
                end_col = e,
                hl_group = "MeterMeterStress",
                hl_mode = "combine",
              })
            end
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

local function build_request(bufnr, ordered_lines, llm_enabled_override, require_llm_source)
  local line_count = vim.api.nvim_buf_line_count(bufnr)

  local lines = {}
  local st = ensure_state(bufnr)

  for _, lnum in ipairs(ordered_lines or {}) do
    if lnum >= 0 and lnum < line_count then
      local text = (vim.api.nvim_buf_get_lines(bufnr, lnum, lnum + 1, false)[1] or "")
      local ok = true
      if ok and not is_scan_line(bufnr, text) then
        ok = false
      end
      local key = cfg.llm.model .. "\n" .. tostring(cfg.llm.endpoint or "") .. "\n" .. text
      local cached = st.cache[key]
      if ok and cached then
        if not require_llm_source then
          ok = false
        elseif cached.source == "llm" then
          ok = false
        end
      end
      if ok then
        table.insert(lines, { lnum = lnum, text = text })
      end
    end
  end

  local llm_cfg = vim.deepcopy(cfg.llm)
  if llm_enabled_override ~= nil then
    llm_cfg.enabled = llm_enabled_override and true or false
  end

  return {
    config = {
      llm = llm_cfg,
    },
    lines = lines,
  }
end

local function merge_cache_and_results(bufnr, resp, ordered_lines)
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
  local line_set = ordered_lines
  if type(line_set) ~= "table" then
    local visible_lines, prefetch_lines = candidate_line_set_for_buf(bufnr)
    line_set = combine_lines(visible_lines, prefetch_lines)
  end
  local line_count = vim.api.nvim_buf_line_count(bufnr)
  for _, lnum in ipairs(line_set) do
    if lnum >= 0 and lnum < line_count then
      local text = (vim.api.nvim_buf_get_lines(bufnr, lnum, lnum + 1, false)[1] or "")
      if is_scan_line(bufnr, text) then
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

local function chunk_lines(lines, chunk_size)
  local out = {}
  local size = tonumber(chunk_size) or 1
  if size < 1 then
    size = 1
  end
  local n = #lines
  local i = 1
  while i <= n do
    local chunk = {}
    local jmax = math.min(n, i + size - 1)
    for j = i, jmax do
      table.insert(chunk, lines[j])
    end
    table.insert(out, chunk)
    i = jmax + 1
  end
  return out
end

local function run_chunk_phase(bufnr, scan_generation, render_lines, lines, chunk_size, llm_enabled, require_llm_source, on_done)
  local chunks = chunk_lines(lines or {}, chunk_size)
  local idx = 1

  local function step()
    local st = ensure_state(bufnr)
    if not vim.api.nvim_buf_is_valid(bufnr) or not st.enabled or st.scan_generation ~= scan_generation then
      return
    end
    if idx > #chunks then
      if on_done then
        on_done()
      end
      return
    end

    local req = build_request(bufnr, chunks[idx], llm_enabled, require_llm_source)
    idx = idx + 1
    if #req.lines == 0 then
      step()
      return
    end

    run_cli(req, function(resp, err)
      local st2 = ensure_state(bufnr)
      if not vim.api.nvim_buf_is_valid(bufnr) or not st2.enabled or st2.scan_generation ~= scan_generation then
        return
      end
      if not err and resp then
        local results = merge_cache_and_results(bufnr, resp, render_lines)
        apply_results(bufnr, results)
      end
      step()
    end)
  end

  step()
end

run_cli = function(input, cb)
  local cmd = default_cli_cmd()
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

  local changedtick = tonumber(vim.api.nvim_buf_get_changedtick(bufnr)) or 0
  if st.scan_running and st.scan_changedtick == changedtick then
    return
  end

  st.scan_running = true
  st.scan_changedtick = changedtick
  st.scan_generation = (tonumber(st.scan_generation) or 0) + 1
  local gen = st.scan_generation

  local visible_lines, prefetch_lines = candidate_line_set_for_buf(bufnr)
  local prioritized_lines = combine_lines(visible_lines, prefetch_lines)
  local all_scan_lines = all_scan_lines_for_buf(bufnr)
  local background_lines = subtract_lines(all_scan_lines, prioritized_lines)
  local render_lines = all_scan_lines

  -- Render cached results immediately to avoid blank states during async work.
  local cached_results = merge_cache_and_results(bufnr, { results = {} }, render_lines)
  apply_results(bufnr, cached_results)
  if #all_scan_lines == 0 then
    st.scan_running = false
    return
  end

  local llm_batch = tonumber(cfg.llm.max_lines_per_scan) or 1
  if llm_batch < 1 then
    llm_batch = 1
  end

  local function finish_scan()
    local st2 = ensure_state(bufnr)
    if st2.scan_generation == gen then
      st2.scan_running = false
    end
  end

  run_chunk_phase(bufnr, gen, render_lines, visible_lines, ENGINE_VISIBLE_CHUNK, false, false, function()
    run_chunk_phase(bufnr, gen, render_lines, prefetch_lines, ENGINE_PREFETCH_CHUNK, false, false, function()
      run_chunk_phase(bufnr, gen, render_lines, background_lines, ENGINE_PREFETCH_CHUNK, false, false, function()
        if not cfg.llm.enabled then
          finish_scan()
          return
        end
        run_chunk_phase(bufnr, gen, render_lines, visible_lines, llm_batch, true, true, function()
          run_chunk_phase(bufnr, gen, render_lines, prefetch_lines, llm_batch, true, true, function()
            run_chunk_phase(bufnr, gen, render_lines, background_lines, llm_batch, true, true, finish_scan)
          end)
        end)
      end)
    end)
  end)
end

local function stop_scan_state(st)
  st.scan_running = false
  st.scan_changedtick = -1
end

local function start_scan(bufnr)
  do_scan(bufnr)
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
      start_scan(bufnr)
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
      start_scan(bufnr)
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
  stop_scan_state(st)
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
  local st = ensure_state(bufnr)
  stop_scan_state(st)
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
      if should_enable(args.buf) then
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
