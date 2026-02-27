local uv = vim.uv or vim.loop
local config = require("metermeter.config")
local cache = require("metermeter.cache")
local filter = require("metermeter.filter")
local render = require("metermeter.render")
local scanner = require("metermeter.scanner")
local subprocess = require("metermeter.subprocess")

local M = {}

local last_notify_time = 0

local function refresh_statusline()
  -- lualine has its own component cache that redrawstatus! alone doesn't always invalidate.
  local ok, lualine = pcall(require, "lualine")
  if ok and type(lualine) == "table" and type(lualine.refresh) == "function" then
    pcall(lualine.refresh)
  end
  vim.cmd("redrawstatus!")
end

M.refresh_statusline = refresh_statusline

function M.default_subprocess_cmd()
  local root = config.plugin_root()
  local script = root .. "/python/metermeter_cli.py"
  -- Prefer the project venv Python (2 levels up from plugin root) over bare python3,
  -- which may resolve to a system Python without prosodic installed.
  local project_root = vim.fn.fnamemodify(root, ":h:h")
  local venv_python = project_root .. "/.venv/bin/python3"

  -- Try venv Python first, then try from cwd (useful in headless testing)
  local python = "python3"
  if vim.fn.executable(venv_python) == 1 then
    python = venv_python
  else
    local cwd_venv = vim.fn.getcwd() .. "/.venv/bin/python3"
    if vim.fn.executable(cwd_venv) == 1 then
      python = cwd_venv
    end
  end
  return { python, script }
end

local function build_request(bufnr, ordered_lines, state_by_buf)
  local line_count = vim.api.nvim_buf_line_count(bufnr)

  local lines = {}
  local st = state_by_buf[bufnr]

  for _, lnum in ipairs(ordered_lines or {}) do
    if lnum >= 0 and lnum < line_count then
      local text = (vim.api.nvim_buf_get_lines(bufnr, lnum, lnum + 1, false)[1] or "")
      if filter.is_scan_line(bufnr, text) and not cache.get(st, cache.key_for_text(st, text)) then
        table.insert(lines, { lnum = lnum, text = text })
      end
    end
  end

  local req = { lines = lines }
  local dominant_meter = tostring(st.dominant_meter or "")
  local dominant_strength = tonumber(st.dominant_strength) or 0
  dominant_strength = math.max(0, math.min(1, dominant_strength))
  if dominant_meter ~= "" and dominant_strength > 0 then
    req.context = {
      dominant_meter = dominant_meter,
      dominant_strength = dominant_strength,
    }
  end
  return req
end

local function merge_cache_and_results(bufnr, resp, ordered_lines, state_by_buf)
  local st = state_by_buf[bufnr]
  if type(resp) ~= "table" or type(resp.results) ~= "table" then
    return {}
  end
  for _, item in ipairs(resp.results) do
    if type(item) == "table" and type(item.text) == "string" then
      local key = cache.key_for_text(st, item.text)
      cache.put(st, key, {
        text = item.text,
        meter_name = item.meter_name or "",
        confidence = item.confidence,
        stress_spans = item.stress_spans or {},
      })
    end
  end
  -- Return results for current visible lines from cache.
  local out = {}
  local cfg = config.cfg
  local line_set = ordered_lines
  if type(line_set) ~= "table" then
    local visible_lines, prefetch_lines = scanner.candidate_line_set_for_buf(bufnr, cfg.prefetch_lines)
    line_set = scanner.combine_lines(visible_lines, prefetch_lines)
  end
  local line_count = vim.api.nvim_buf_line_count(bufnr)
  for _, lnum in ipairs(line_set) do
    if lnum >= 0 and lnum < line_count then
      local text = (vim.api.nvim_buf_get_lines(bufnr, lnum, lnum + 1, false)[1] or "")
      if filter.is_scan_line(bufnr, text) then
        local key = cache.key_for_text(st, text)
        local cached = cache.get(st, key)
        if cached then
          table.insert(out, {
            lnum = lnum,
            text = text,
            meter_name = cached.meter_name or "",
            confidence = cached.confidence,
            stress_spans = cached.stress_spans or {},
          })
        end
      end
    end
  end
  table.sort(out, function(a, b)
    return (a.lnum or 0) < (b.lnum or 0)
  end)

  -- Compute dominant meter from cached results.
  local counts = {}
  local total = 0
  for _, item in ipairs(out) do
    local meter = item.meter_name or ""
    if meter ~= "" then
      local conf = tonumber(item.confidence) or 0.5
      conf = math.max(0.05, math.min(1.0, conf))
      counts[meter] = (counts[meter] or 0) + conf
      total = total + conf
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
  st.dominant_meter = best_meter
  if total > 0 then
    st.dominant_strength = best_weight / total
  else
    st.dominant_strength = 0
  end

  return out
end

local function maybe_apply_results(bufnr, results, state_by_buf)
  local st = state_by_buf[bufnr]
  -- Sig based on content: lnum+meter_name per result, so switching back to a
  -- cached line with different meter triggers a re-render even when cache_write_seq
  -- and result count haven't changed.
  local parts = {}
  for _, item in ipairs(results or {}) do
    parts[#parts + 1] = tostring(item.lnum) .. "=" .. (item.meter_name or "")
  end
  local sig = table.concat(parts, ",")
  if sig == (st.last_render_sig or "") then
    return
  end
  st.last_render_sig = sig
  st.debug_apply_count = (tonumber(st.debug_apply_count) or 0) + 1
  render.apply_results(bufnr, results)
  refresh_statusline()
end

local function run_phase(bufnr, scan_generation, render_lines, lines, on_done, state_by_buf, subprocess_cmd)
  local st = state_by_buf[bufnr]
  if not st or not vim.api.nvim_buf_is_valid(bufnr) or not st.enabled or st.scan_generation ~= scan_generation then
    if on_done then
      on_done()
    end
    return
  end

  local req = build_request(bufnr, lines, state_by_buf)
  if #req.lines == 0 then
    if on_done then
      on_done()
    end
    return
  end

  st.debug_cli_count = (tonumber(st.debug_cli_count) or 0) + 1

  local cmd = subprocess_cmd or M.default_subprocess_cmd()
  if not subprocess.ensure_running(cmd) then
    st.last_error = "failed to start metermeter subprocess (restart limit reached)"
    local now = uv.now() / 1000
    if now - last_notify_time > 5 then
      last_notify_time = now
      vim.notify("MeterMeter: " .. st.last_error, vim.log.levels.WARN)
    end
    if on_done then
      on_done()
    end
    return
  end

  subprocess.send(req, function(resp, err)
    local st2 = state_by_buf[bufnr]
    if not st2 then
      if on_done then
        on_done()
      end
      return
    end
    if not vim.api.nvim_buf_is_valid(bufnr) or not st2.enabled or st2.scan_generation ~= scan_generation then
      if on_done then
        on_done()
      end
      return
    end
    if err then
      st2.last_error = err
    elseif resp then
      st2.last_error = nil
      local results = merge_cache_and_results(bufnr, resp, render_lines, state_by_buf)
      maybe_apply_results(bufnr, results, state_by_buf)
    end

    -- Remove lines just processed from pending
    local processed = {}
    for _, item in ipairs(req.lines or {}) do
      processed[item.lnum] = true
    end
    local remaining = {}
    local remaining_keys = {}
    for _, lnum in ipairs(st2.pending_lnums or {}) do
      if processed[lnum] then
        if st2.pending_keys then
          st2.pending_keys[lnum] = nil
        end
      else
        local key = st2.pending_keys and st2.pending_keys[lnum]
        if key and st2.cache and st2.cache[key] ~= nil then
          -- This line is now cached (e.g. duplicate text elsewhere), so it no longer needs analysis.
          if st2.pending_keys then
            st2.pending_keys[lnum] = nil
          end
        else
          remaining[#remaining + 1] = lnum
          if key then
            remaining_keys[lnum] = key
          end
        end
      end
    end
    st2.pending_lnums = remaining
    st2.pending_keys = remaining_keys
    if #remaining > 0 and config.cfg.ui.loading_indicator then
      render.refresh_loading(bufnr, state_by_buf)
    else
      render.stop_loading(bufnr, state_by_buf)
    end

    if on_done then
      on_done()
    end
  end)
end

function M.do_scan(bufnr, state_by_buf, subprocess_cmd)
  if not vim.api.nvim_buf_is_valid(bufnr) then
    return
  end
  local st = state_by_buf[bufnr]
  if not st or not st.enabled then
    return
  end

  local cfg = config.cfg
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
  local all_scan_lines = scanner.all_scan_lines_for_buf(bufnr, filter.is_scan_line)
  local background_lines = scanner.subtract_lines(all_scan_lines, prioritized_lines)
  local render_lines = all_scan_lines

  -- Render cached results immediately to avoid blank states during async work.
  local cached_results = merge_cache_and_results(bufnr, { results = {} }, render_lines, state_by_buf)
  maybe_apply_results(bufnr, cached_results, state_by_buf)
  if #all_scan_lines == 0 then
    st.scan_running = false
    return
  end

  -- Compute uncached lines that still need analysis
  local pending = {}
  local pending_keys = {}
  local line_count = vim.api.nvim_buf_line_count(bufnr)
  for _, lnum in ipairs(all_scan_lines) do
    if lnum >= 0 and lnum < line_count then
      local text = (vim.api.nvim_buf_get_lines(bufnr, lnum, lnum + 1, false)[1] or "")
      local key = cache.key_for_text(st, text)
      if not cache.get(st, key) then
        pending[#pending + 1] = lnum
        pending_keys[lnum] = key
      end
    end
  end
  st.pending_lnums = pending
  st.pending_keys = pending_keys
  if cfg.ui.loading_indicator then
    render.refresh_loading(bufnr, state_by_buf)
  end

  local function finish_scan()
    if state_by_buf[bufnr] and state_by_buf[bufnr].scan_generation == gen then
      state_by_buf[bufnr].scan_running = false
    end
  end

  run_phase(bufnr, gen, render_lines, visible_lines, function()
    run_phase(bufnr, gen, render_lines, prefetch_lines, function()
      run_phase(bufnr, gen, render_lines, background_lines, function()
        finish_scan()
      end, state_by_buf, subprocess_cmd)
    end, state_by_buf, subprocess_cmd)
  end, state_by_buf, subprocess_cmd)
end

return M
