local uv = vim.uv or vim.loop

local M = {}
local scanner = require("metermeter.scanner")
local state_mod = require("metermeter.state")

local ns = vim.api.nvim_create_namespace("metermeter")

local DEFAULTS = {
  debounce_ms = 80,
  rescan_interval_ms = 0,
  prefetch_lines = 5, -- also scan around cursor, not only visible lines

  ui = {
    stress = true,
    meter_hints = true,
    meter_hint_confidence_levels = 6, -- number of discrete tint steps (>= 2)
    show_error_hint = true,
  },

  -- If true, only annotate lines that end with a trailing "\" (useful for mixed-format files).
  -- If false (default), annotate every non-comment line.
  require_trailing_backslash = false,

  -- LLM analysis (required for meter output).
  llm = {
    enabled = true,
    endpoint = "http://127.0.0.1:11434/v1/chat/completions",
    model = "qwen2.5:7b-instruct",
    timeout_ms = 30000,
    temperature = 0.1,
    eval_mode = "production",
    max_lines_per_scan = 2,
    max_concurrent = 1,
    api_key = "",
    failure_threshold = 3,
    cooldown_ms = 15000,
  },

  cache = {
    max_entries = 5000,
  },

  lexicon_path = "",
  extra_lexicon_path = "",

  debug_dump_path = "/tmp/metermeter_nvim_dump.json",
}

local cfg = vim.deepcopy(DEFAULTS)

local state_by_buf = {}
local run_cli

local function _cache_max_entries()
  local n = tonumber(cfg.cache and cfg.cache.max_entries) or 5000
  if n < 100 then
    n = 100
  end
  return math.floor(n)
end

local function plugin_root()
  local src = debug.getinfo(1, "S").source
  local path = src:sub(2) -- drop leading "@"
  return vim.fn.fnamemodify(path, ":p:h:h:h") -- .../lua/metermeter/init.lua -> plugin root
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
  if vim.bo[bufnr].buftype ~= "" then
    return false
  end

  -- Modeline-friendly: user can opt-in via filetype.
  -- Example modeline: `vim: set ft=typst.metermeter :`
  return has_ft_token(bufnr, "metermeter")
end

local function should_run_for_buf(bufnr)
  local st = state_by_buf[bufnr]
  if st and st.user_enabled ~= nil then
    return st.user_enabled and true or false
  end
  return should_enable(bufnr)
end

local function compute_stress_hl()
  -- Bold overlay so it stays visible even when Normal bg is pure black.
  vim.api.nvim_set_hl(0, "MeterMeterStress", { bold = true })
  vim.api.nvim_set_hl(0, "MeterMeterEOL", { link = "Comment" })
  vim.api.nvim_set_hl(0, "MeterMeterError", { link = "WarningMsg" })
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

local function _clamp_levels()
  local levels = tonumber(cfg.ui.meter_hint_confidence_levels) or 6
  if levels < 2 then levels = 2 end
  if levels > 12 then levels = 12 end
  return levels
end

local function compute_eol_hls()
  -- Confidence-driven meter hint: more confident => closer to Normal, less confident => closer to Comment.
  local levels = _clamp_levels()

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
  local levels = _clamp_levels()
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
  local require_backslash = cfg.require_trailing_backslash and true or false
  local bval = vim.b[bufnr] and vim.b[bufnr].metermeter_require_trailing_backslash
  if bval ~= nil then
    require_backslash = (bval == true or bval == 1 or bval == "1")
  else
    local gval = vim.g.metermeter_require_trailing_backslash
    if gval ~= nil then
      require_backslash = (gval == true or gval == 1 or gval == "1")
    end
  end
  if require_backslash then
    return s:match("\\$") ~= nil
  end
  return true
end

local function ensure_state(bufnr)
  local st = state_by_buf[bufnr]
  if st then
    return st
  end
  st = state_mod.new_state()
  state_by_buf[bufnr] = st
  return st
end

local function _cache_touch(st, entry)
  st.cache_seq = (tonumber(st.cache_seq) or 0) + 1
  entry.at = st.cache_seq
end

local function _cache_get(st, key)
  local entry = st.cache[key]
  if not entry then
    return nil
  end
  _cache_touch(st, entry)
  return entry.payload
end

local function _cache_put(st, key, payload)
  local entry = st.cache[key]
  if not entry then
    entry = { payload = payload, at = 0 }
    st.cache[key] = entry
    st.cache_size = (tonumber(st.cache_size) or 0) + 1
  else
    entry.payload = payload
  end
  _cache_touch(st, entry)
  st.cache_write_seq = (tonumber(st.cache_write_seq) or 0) + 1

  local max_entries = _cache_max_entries()
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

local function _cache_key_for_text(st, text)
  local epoch = 0
  if st and st.cache_epoch then
    epoch = tonumber(st.cache_epoch) or 0
  end
  return table.concat({
    tostring(cfg.llm.model or ""),
    tostring(cfg.llm.endpoint or ""),
    tostring(cfg.llm.eval_mode or ""),
    tostring(cfg.llm.temperature or ""),
    tostring(cfg.lexicon_path or ""),
    tostring(cfg.extra_lexicon_path or ""),
    tostring(epoch),
    text,
  }, "\n")
end

local function _llm_in_cooldown(st)
  local now = uv.now()
  return now < (tonumber(st.llm_cooldown_until) or 0)
end

local function _llm_record_failure(st, msg)
  st.llm_fail_count = (tonumber(st.llm_fail_count) or 0) + 1
  if type(msg) == "string" and msg ~= "" then
    st.llm_error = msg
  end
  local threshold = tonumber(cfg.llm and cfg.llm.failure_threshold) or 3
  if threshold < 1 then
    threshold = 1
  end
  if st.llm_fail_count >= threshold then
    local cool = tonumber(cfg.llm and cfg.llm.cooldown_ms) or 15000
    if cool < 0 then
      cool = 0
    end
    st.llm_cooldown_until = uv.now() + cool
    st.llm_fail_count = 0
  end
end

local function _llm_record_success(st)
  st.llm_fail_count = 0
  st.llm_error = ""
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
      local conf = item.confidence
      if type(conf) == "number" then
        label = tostring(item.meter_name or label or "")
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
    end
  end

  if cfg.ui.show_error_hint and #results == 0 then
    local st = ensure_state(bufnr)
    local msg = tostring(st.llm_error or "")
    if msg ~= "" then
      local line_count = vim.api.nvim_buf_line_count(bufnr)
      if line_count > 0 then
        local line = 0
        for i = 0, line_count - 1 do
          local text = (vim.api.nvim_buf_get_lines(bufnr, i, i + 1, false)[1] or "")
          if is_scan_line(bufnr, text) then
            line = i
            break
          end
        end
        msg = msg:gsub("%s+", " ")
        if #msg > 120 then
          msg = msg:sub(1, 117) .. "..."
        end
        vim.api.nvim_buf_set_extmark(bufnr, ns, line, 0, {
          virt_text = { { " MeterMeter LLM error: " .. msg, "MeterMeterError" } },
          virt_text_pos = "eol",
        })
      end
    end
  end
end

local function maybe_apply_results(bufnr, results)
  local st = ensure_state(bufnr)
  local sig = tostring(st.cache_write_seq or 0) .. ":" .. tostring(results and #results or 0)
  if sig == (st.last_render_sig or "") then
    return
  end
  st.last_render_sig = sig
  st.debug_apply_count = (tonumber(st.debug_apply_count) or 0) + 1
  apply_results(bufnr, results)
end

local function dominant_context_for_lines(bufnr, line_set)
  local st = ensure_state(bufnr)
  local counts = {}
  local total = 0
  local seen = 0
  local line_count = vim.api.nvim_buf_line_count(bufnr)
  for _, lnum in ipairs(line_set or {}) do
    if lnum >= 0 and lnum < line_count then
      local text = (vim.api.nvim_buf_get_lines(bufnr, lnum, lnum + 1, false)[1] or "")
      if is_scan_line(bufnr, text) then
        local key = _cache_key_for_text(st, text)
        local cached = _cache_get(st, key)
        if cached and type(cached.meter_name) == "string" and cached.meter_name ~= "" then
          local meter = string.lower(vim.trim(cached.meter_name))
          local conf = tonumber(cached.confidence) or 0.5
          conf = math.max(0.05, math.min(1.0, conf))
          counts[meter] = (counts[meter] or 0) + conf
          total = total + conf
          seen = seen + 1
        end
      end
    end
  end

  local best_meter = ""
  local best_weight = 0
  for meter, weight in pairs(counts) do
    if weight > best_weight then
      best_meter = meter
      best_weight = weight
    end
  end

  local ratio = 0
  if total > 0 then
    ratio = best_weight / total
  end

  local min_ratio = 0.75
  local min_lines = 6
  if best_meter ~= "" and best_meter ~= st.dominant_meter and ratio >= min_ratio and seen >= min_lines then
    st.cache_epoch = (tonumber(st.cache_epoch) or 0) + 1
  end
  st.dominant_meter = best_meter
  st.dominant_ratio = ratio
  st.dominant_line_count = seen
  return {
    dominant_meter = best_meter,
    dominant_ratio = ratio,
    dominant_line_count = seen,
  }
end

local function build_request(bufnr, ordered_lines, context)
  local line_count = vim.api.nvim_buf_line_count(bufnr)

  local lines = {}
  local st = ensure_state(bufnr)

  for _, lnum in ipairs(ordered_lines or {}) do
    if lnum >= 0 and lnum < line_count then
      local text = (vim.api.nvim_buf_get_lines(bufnr, lnum, lnum + 1, false)[1] or "")
      if is_scan_line(bufnr, text) and not _cache_get(st, _cache_key_for_text(st, text)) then
        table.insert(lines, { lnum = lnum, text = text })
      end
    end
  end

  return {
    config = {
      llm = vim.deepcopy(cfg.llm),
      context = context or {},
      lexicon_path = cfg.lexicon_path or "",
      extra_lexicon_path = cfg.extra_lexicon_path or "",
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
      local key = _cache_key_for_text(st, item.text)
      _cache_put(st, key, {
        text = item.text,
        meter_name = item.meter_name or "",
        confidence = item.confidence,
        token_patterns = item.token_patterns or {},
        stress_spans = item.stress_spans or {},
      })
    end
  end
  -- Return results for current visible lines from cache.
  local out = {}
  local line_set = ordered_lines
  if type(line_set) ~= "table" then
    local visible_lines, prefetch_lines = scanner.candidate_line_set_for_buf(bufnr, cfg.prefetch_lines)
    line_set = scanner.combine_lines(visible_lines, prefetch_lines)
  end
  local line_count = vim.api.nvim_buf_line_count(bufnr)
  for _, lnum in ipairs(line_set) do
    if lnum >= 0 and lnum < line_count then
      local text = (vim.api.nvim_buf_get_lines(bufnr, lnum, lnum + 1, false)[1] or "")
      if is_scan_line(bufnr, text) then
        local key = _cache_key_for_text(st, text)
        local cached = _cache_get(st, key)
        if cached then
          table.insert(out, {
            lnum = lnum,
            text = text,
            meter_name = cached.meter_name or "",
            confidence = cached.confidence,
            token_patterns = cached.token_patterns or {},
            stress_spans = cached.stress_spans or {},
          })
        end
      end
    end
  end
  table.sort(out, function(a, b)
    return (a.lnum or 0) < (b.lnum or 0)
  end)
  return out
end

local function run_chunk_phase(bufnr, scan_generation, render_lines, lines, chunk_size, context_fn, on_done)
  local chunks = scanner.chunk_lines(lines or {}, chunk_size)
  local idx = 1
  local inflight = 0
  local done = false
  local max_concurrent = tonumber(cfg.llm and cfg.llm.max_concurrent) or 1
  if max_concurrent < 1 then
    max_concurrent = 1
  end

  local function finish_if_done()
    if done then
      return
    end
    if idx > #chunks and inflight == 0 then
      done = true
      if on_done then
        on_done()
      end
    end
  end

  local function launch_next()
    local st = ensure_state(bufnr)
    if not vim.api.nvim_buf_is_valid(bufnr) or not st.enabled or st.scan_generation ~= scan_generation then
      return
    end
    while inflight < max_concurrent and idx <= #chunks do
      local context = {}
      if type(context_fn) == "function" then
        local ok_ctx, built_ctx = pcall(context_fn)
        if ok_ctx and type(built_ctx) == "table" then
          context = built_ctx
        end
      end
      local req = build_request(bufnr, chunks[idx], context)
      idx = idx + 1
      if #req.lines == 0 then
        goto continue
      end

      st.debug_cli_count = (tonumber(st.debug_cli_count) or 0) + 1
      st.debug_llm_cli_count = (tonumber(st.debug_llm_cli_count) or 0) + 1
      inflight = inflight + 1
      run_cli(req, function(resp, err)
        inflight = inflight - 1
        local st2 = ensure_state(bufnr)
        if not vim.api.nvim_buf_is_valid(bufnr) or not st2.enabled or st2.scan_generation ~= scan_generation then
          finish_if_done()
          return
        end
        if not err and resp then
          if type(resp.error) == "string" and resp.error ~= "" then
            _llm_record_failure(st2, resp.error)
            maybe_apply_results(bufnr, {})
            finish_if_done()
            return
          end
          _llm_record_success(st2)
          local results = merge_cache_and_results(bufnr, resp, render_lines)
          maybe_apply_results(bufnr, results)
        elseif err then
          _llm_record_failure(st2, err)
          maybe_apply_results(bufnr, {})
        end
        launch_next()
        finish_if_done()
      end)
      ::continue::
    end
    finish_if_done()
  end

  launch_next()
end

run_cli = function(input, cb)
  local cmd = default_cli_cmd()
  local text = vim.json.encode(input)
  vim.system(cmd, { stdin = text, text = true }, function(res)
    -- vim.system callbacks run in a "fast event" context; bounce back to main loop.
    vim.schedule(function()
      if res.code ~= 0 then
        cb(nil, "cli exited " .. tostring(res.code) .. ": " .. (res.stderr or ""))
        return
      end
      local ok, obj = pcall(vim.json.decode, res.stdout or "")
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
  if not should_run_for_buf(bufnr) then
    return
  end
  if not cfg.llm.enabled then
    st.llm_error = "LLM mode disabled; set llm.enabled=true"
    maybe_apply_results(bufnr, {})
    return
  end

  local changedtick = tonumber(vim.api.nvim_buf_get_changedtick(bufnr)) or 0
  local view_sig = scanner.viewport_signature(bufnr)
  if (not st.scan_running) and st.last_changedtick == changedtick and st.last_view_sig == view_sig then
    return
  end
  if st.scan_running and st.scan_changedtick == changedtick then
    return
  end

  st.scan_running = true
  st.debug_scan_count = (tonumber(st.debug_scan_count) or 0) + 1
  st.scan_changedtick = changedtick
  st.last_changedtick = changedtick
  st.last_view_sig = view_sig
  st.scan_generation = (tonumber(st.scan_generation) or 0) + 1
  local gen = st.scan_generation

  local visible_lines, prefetch_lines = scanner.candidate_line_set_for_buf(bufnr, cfg.prefetch_lines)
  local prioritized_lines = scanner.combine_lines(visible_lines, prefetch_lines)
  local all_scan_lines = scanner.all_scan_lines_for_buf(bufnr, is_scan_line)
  local background_lines = scanner.subtract_lines(all_scan_lines, prioritized_lines)
  local render_lines = all_scan_lines

  -- Render cached results immediately (LLM-only) to avoid blank states during async work.
  local cached_results = merge_cache_and_results(bufnr, { results = {} }, render_lines)
  maybe_apply_results(bufnr, cached_results)
  if #all_scan_lines == 0 then
    st.scan_running = false
    return
  end

  local llm_batch = tonumber(cfg.llm.max_lines_per_scan) or 1
  if llm_batch < 1 then
    llm_batch = 1
  end
  local llm_needs_refresh = (st.last_llm_changedtick ~= changedtick) or (st.last_llm_view_sig ~= view_sig)

  local function finish_scan()
    local st2 = ensure_state(bufnr)
    if st2.scan_generation == gen then
      st2.scan_running = false
    end
  end

  if _llm_in_cooldown(st) or (not llm_needs_refresh) then
    finish_scan()
    return
  end

  local function context_fn()
    return dominant_context_for_lines(bufnr, all_scan_lines)
  end

  run_chunk_phase(bufnr, gen, render_lines, visible_lines, llm_batch, context_fn, function()
    run_chunk_phase(bufnr, gen, render_lines, prefetch_lines, llm_batch, context_fn, function()
      run_chunk_phase(bufnr, gen, render_lines, background_lines, llm_batch, context_fn, function()
        local st3 = ensure_state(bufnr)
        if st3.scan_generation == gen then
          st3.last_llm_changedtick = changedtick
          st3.last_llm_view_sig = view_sig
        end
        finish_scan()
      end)
    end)
  end)
end

local function _cleanup_timers(st)
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
end

local function cleanup_buf(bufnr)
  local st = state_by_buf[bufnr]
  if not st then
    return
  end
  state_mod.stop_scan_state(st)
  _cleanup_timers(st)
  clear_buf(bufnr)
  state_by_buf[bufnr] = nil
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
  st.last_changedtick = -1
  st.last_view_sig = ""
  ensure_tick(bufnr)
  schedule_scan(bufnr)
end

function M.disable(bufnr)
  bufnr = (bufnr == 0) and vim.api.nvim_get_current_buf() or bufnr
  local st = ensure_state(bufnr)
  st.enabled = false
  state_mod.stop_scan_state(st)
  _cleanup_timers(st)
  clear_buf(bufnr)
end

function M.toggle(bufnr)
  bufnr = (bufnr == 0) and vim.api.nvim_get_current_buf() or bufnr
  local st = ensure_state(bufnr)
  local next_enabled = not should_run_for_buf(bufnr)
  st.user_enabled = next_enabled
  if next_enabled then
    M.enable(bufnr)
  else
    M.disable(bufnr)
  end
end

function M.status(bufnr)
  bufnr = (bufnr == 0) and vim.api.nvim_get_current_buf() or bufnr
  local st = ensure_state(bufnr)
  local auto = should_enable(bufnr)
  local effective = should_run_for_buf(bufnr)
  local override = st.user_enabled
  local cool_ms = math.max(0, (tonumber(st.llm_cooldown_until) or 0) - uv.now())
  local msg = string.format(
    "MeterMeter status: enabled=%s effective=%s auto=%s override=%s llm_cooldown_ms=%d llm_calls=%d dominant=%s ratio=%.2f lines=%d llm_error=%s",
    tostring(st.enabled),
    tostring(effective),
    tostring(auto),
    tostring(override),
    math.floor(cool_ms),
    tonumber(st.debug_llm_cli_count) or 0,
    tostring(st.dominant_meter or ""),
    tonumber(st.dominant_ratio) or 0,
    tonumber(st.dominant_line_count) or 0,
    tostring(st.llm_error or "")
  )
  vim.notify(msg, vim.log.levels.INFO)
end

function M.statusline(bufnr)
  bufnr = (bufnr == 0 or bufnr == nil) and vim.api.nvim_get_current_buf() or bufnr
  local st = state_by_buf[bufnr]
  if not st or not st.enabled then
    return ""
  end
  local err = tostring(st.llm_error or "")
  if err ~= "" then
    -- Compact the error for statusline display.
    err = err:gsub("%s+", " ")
    if #err > 60 then
      err = err:sub(1, 57) .. "..."
    end
    return "MM: " .. err
  end
  local meter = tostring(st.dominant_meter or "")
  if meter ~= "" then
    return "MM: " .. meter
  end
  if st.scan_running then
    return "MM: scanning"
  end
  return "MM"
end

function M._debug_stats(bufnr)
  bufnr = (bufnr == 0) and vim.api.nvim_get_current_buf() or bufnr
  local st = ensure_state(bufnr)
  return {
    scan_count = tonumber(st.debug_scan_count) or 0,
    cli_count = tonumber(st.debug_cli_count) or 0,
    llm_cli_count = tonumber(st.debug_llm_cli_count) or 0,
    apply_count = tonumber(st.debug_apply_count) or 0,
    enabled = st.enabled and true or false,
    scan_running = st.scan_running and true or false,
  }
end

function M.rescan(bufnr)
  bufnr = (bufnr == 0) and vim.api.nvim_get_current_buf() or bufnr
  local st = ensure_state(bufnr)
  state_mod.stop_scan_state(st)
  schedule_scan(bufnr)
end

function M.dump_debug(bufnr)
  bufnr = (bufnr == 0) and vim.api.nvim_get_current_buf() or bufnr
  local st = ensure_state(bufnr)
  local marks = vim.api.nvim_buf_get_extmarks(bufnr, ns, 0, -1, { details = true })
  local out = {
    ts = os.time(),
    bufnr = bufnr,
    file = vim.api.nvim_buf_get_name(bufnr) or "",
    enabled = st.enabled,
    extmarks = marks,
  }
  local path = cfg.debug_dump_path or "/tmp/metermeter_nvim_dump.json"
  local ok, enc = pcall(vim.json.encode, out)
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
      if should_run_for_buf(args.buf) then
        M.enable(args.buf)
      end
    end,
  })
  vim.api.nvim_create_autocmd("FileType", {
    group = group,
    callback = function(args)
      if should_run_for_buf(args.buf) then
        M.enable(args.buf)
      else
        local st = state_by_buf[args.buf]
        if st and st.enabled then
          M.disable(args.buf)
        end
      end
    end,
  })
  vim.api.nvim_create_autocmd({ "TextChanged", "TextChangedI", "WinScrolled" }, {
    group = group,
    callback = function(args)
      local st = ensure_state(args.buf)
      if st.enabled then
        schedule_scan(args.buf)
      end
    end,
  })
  vim.api.nvim_create_autocmd({ "BufWipeout", "BufDelete" }, {
    group = group,
    callback = function(args)
      cleanup_buf(args.buf)
    end,
  })

  -- Initial bootstrap: if setup runs after files are already open, ensure those buffers are
  -- enabled/scanned without requiring a manual rescan.
  for _, bufnr in ipairs(vim.api.nvim_list_bufs()) do
    if vim.api.nvim_buf_is_valid(bufnr) and vim.api.nvim_buf_is_loaded(bufnr) and should_run_for_buf(bufnr) then
      M.enable(bufnr)
    end
  end
end

return M
