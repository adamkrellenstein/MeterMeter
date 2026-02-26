local uv = vim.uv or vim.loop

local M = {}
local config = require("metermeter.config")
local engine = require("metermeter.engine")
local filter = require("metermeter.filter")
local highlight = require("metermeter.highlight")
local render = require("metermeter.render")
local state_mod = require("metermeter.state")
local subprocess = require("metermeter.subprocess")

local state_by_buf = {}
local subprocess_cmd

local function ensure_state(bufnr)
  local st = state_by_buf[bufnr]
  if st then
    return st
  end
  st = state_mod.new_state()
  state_by_buf[bufnr] = st
  return st
end

local function should_run_for_buf(bufnr)
  local st = state_by_buf[bufnr]
  if st and st.user_enabled ~= nil then
    return st.user_enabled and true or false
  end
  return filter.should_enable(bufnr)
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
  render.stop_loading(bufnr, state_by_buf)
  state_mod.stop_scan_state(st)
  _cleanup_timers(st)
  render.clear_buf(bufnr)
  state_by_buf[bufnr] = nil
end

local function start_scan(bufnr)
  engine.do_scan(bufnr, state_by_buf, subprocess_cmd)
end

local function schedule_scan(bufnr)
  local st = ensure_state(bufnr)
  if st.timer then
    st.timer:stop()
    st.timer:close()
    st.timer = nil
  end
  st.timer = uv.new_timer()
  local ms = tonumber(config.cfg.debounce_ms) or 80
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
  local ms = tonumber(config.cfg.rescan_interval_ms) or 0
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

---@param bufnr integer Buffer number (0 for current buffer)
function M.enable(bufnr)
  bufnr = (bufnr == 0) and vim.api.nvim_get_current_buf() or bufnr
  local st = ensure_state(bufnr)
  st.enabled = true
  st.last_changedtick = -1
  st.last_view_sig = ""
  engine.refresh_statusline()
  ensure_tick(bufnr)
  schedule_scan(bufnr)
end

---@param bufnr integer Buffer number (0 for current buffer)
function M.disable(bufnr)
  bufnr = (bufnr == 0) and vim.api.nvim_get_current_buf() or bufnr
  local st = ensure_state(bufnr)
  st.enabled = false
  render.stop_loading(bufnr, state_by_buf)
  state_mod.stop_scan_state(st)
  _cleanup_timers(st)
  render.clear_buf(bufnr)
  engine.refresh_statusline()
end

---@param bufnr integer Buffer number (0 for current buffer)
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

---@param bufnr? integer Buffer number (0 or nil for current buffer)
---@return string
function M.statusline(bufnr)
  bufnr = (bufnr == 0 or bufnr == nil) and vim.api.nvim_get_current_buf() or bufnr
  local st = state_by_buf[bufnr]
  if not st or not st.enabled then
    return ""
  end
  if st.last_error then
    local msg = tostring(st.last_error)
    local short = msg:match("[%w]*Error[%w]*:[^\n]*") or msg:sub(1, 60)
    return "MM: error: " .. short
  end
  local meter = tostring(st.dominant_meter or "")
  return "MM: " .. (meter ~= "" and meter or "…")
end

function M._debug_stats(bufnr)
  bufnr = (bufnr == 0) and vim.api.nvim_get_current_buf() or bufnr
  local st = ensure_state(bufnr)
  return {
    scan_count = tonumber(st.debug_scan_count) or 0,
    cli_count = tonumber(st.debug_cli_count) or 0,
    apply_count = tonumber(st.debug_apply_count) or 0,
    enabled = st.enabled and true or false,
    scan_running = st.scan_running and true or false,
  }
end

---@param bufnr integer Buffer number (0 for current buffer)
function M.rescan(bufnr)
  bufnr = (bufnr == 0) and vim.api.nvim_get_current_buf() or bufnr
  local st = ensure_state(bufnr)
  -- Bump cache_epoch so all cached results are invalidated and lines are re-analyzed from scratch.
  st.cache_epoch = (tonumber(st.cache_epoch) or 0) + 1
  st.last_render_sig = ""
  state_mod.stop_scan_state(st)
  schedule_scan(bufnr)
end

---@param bufnr integer Buffer number (0 for current buffer)
function M.debug_dump(bufnr)
  bufnr = (bufnr == 0) and vim.api.nvim_get_current_buf() or bufnr
  local st = ensure_state(bufnr)
  local ft = (vim.bo[bufnr] and vim.bo[bufnr].filetype) or ""
  local auto_on = filter.should_enable(bufnr)

  -- Always emit a summary line so this is useful even when disabled.
  local summary
  if st.last_error then
    summary = ("enabled=%s ft=%s error=%s"):format(tostring(st.enabled), ft, tostring(st.last_error):match("[^\n]*"))
  elseif not st.enabled then
    local reason = auto_on and "manually disabled" or ("ft=%q has no metermeter token"):format(ft)
    summary = ("enabled=false  %s"):format(reason)
  else
    summary = ("enabled=true  ft=%s  dominant=%s  scans=%d  requests=%d"):format(
      ft,
      st.dominant_meter ~= "" and st.dominant_meter or "(none yet)",
      tonumber(st.debug_scan_count) or 0,
      tonumber(st.debug_cli_count) or 0
    )
  end
  local sl = M.statusline(bufnr)
  summary = summary .. ("  statusline()=%q"):format(sl)
  vim.notify("MeterMeter: " .. summary, vim.log.levels.INFO)

  local marks = vim.api.nvim_buf_get_extmarks(bufnr, render.ns, 0, -1, { details = true })
  local out = {
    ts = os.time(),
    bufnr = bufnr,
    file = vim.api.nvim_buf_get_name(bufnr) or "",
    filetype = ft,
    enabled = st.enabled,
    auto_enable = auto_on,
    user_enabled = st.user_enabled,
    dominant_meter = st.dominant_meter,
    dominant_strength = st.dominant_strength,
    last_error = st.last_error,
    debug_scan_count = st.debug_scan_count,
    debug_cli_count = st.debug_cli_count,
    extmarks = marks,
  }
  local cfg = config.cfg
  local path = cfg.debug_dump_path or "/tmp/metermeter_nvim_dump.json"
  local ok, enc = pcall(vim.json.encode, out)
  if ok then
    local f = io.open(path, "w")
    if f then
      f:write(enc)
      f:close()
      vim.notify("MeterMeter: full dump written to " .. path, vim.log.levels.INFO)
    else
      vim.notify("MeterMeter: could not write to " .. path, vim.log.levels.WARN)
    end
  else
    vim.notify("MeterMeter: failed to encode dump: " .. tostring(enc), vim.log.levels.WARN)
  end
end

---@param opts? table Configuration overrides (see DEFAULTS)
function M.setup(opts)
  if vim.fn.has("nvim-0.10") ~= 1 then
    vim.notify("metermeter.nvim requires Neovim 0.10+", vim.log.levels.ERROR)
    return
  end

  config.apply(opts)
  subprocess_cmd = engine.default_subprocess_cmd()

  highlight.compute_stress_hl()
  highlight.compute_eol_hls()
  vim.api.nvim_create_autocmd("ColorScheme", {
    group = vim.api.nvim_create_augroup("MeterMeterColors", { clear = true }),
    callback = function()
      highlight.compute_stress_hl()
      highlight.compute_eol_hls()
    end,
  })

  local group = vim.api.nvim_create_augroup("MeterMeter", { clear = true })
  vim.api.nvim_create_autocmd("VimLeavePre", {
    group = group,
    callback = function()
      subprocess.shutdown()
    end,
  })
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

  -- After all plugins load: inject into lualine if present, otherwise patch each
  -- metermeter window's statusline directly so no configuration is ever needed.
  vim.api.nvim_create_autocmd("VimEnter", {
    once = true,
    group = group,
    callback = function()
      if vim.g._metermeter_lualine_injected then
        return
      end

      local lualine_ok, lualine = pcall(require, "lualine")
      if
        lualine_ok
        and type(lualine) == "table"
        and type(lualine.get_config) == "function"
        and type(lualine.setup) == "function"
      then
        local lualine_cfg = lualine.get_config()
        if lualine_cfg then
          vim.g._metermeter_lualine_injected = true
          local comp = function()
            return M.statusline()
          end
          lualine_cfg.sections = lualine_cfg.sections or {}
          lualine_cfg.sections.lualine_x = lualine_cfg.sections.lualine_x or {}
          table.insert(lualine_cfg.sections.lualine_x, 1, comp)
          lualine_cfg.inactive_sections = lualine_cfg.inactive_sections or {}
          lualine_cfg.inactive_sections.lualine_x = lualine_cfg.inactive_sections.lualine_x or {}
          table.insert(lualine_cfg.inactive_sections.lualine_x, 1, comp)
          lualine.setup(lualine_cfg)
          return
        end
      end

      -- No lualine: set the statusline for every window that shows an enabled buffer.
      -- Only touch windows that are using the global default (empty local statusline).
      local function patch_win(win)
        if vim.api.nvim_win_is_valid(win) and vim.wo[win].statusline == "" then
          vim.wo[win].statusline = " %f%m  %{v:lua.require('metermeter').statusline()}  %=%-14.(%l,%c%V%)  %P "
        end
      end
      for _, st_bufnr in ipairs(vim.api.nvim_list_bufs()) do
        local st = state_by_buf[st_bufnr]
        if st and st.enabled then
          for _, win in ipairs(vim.fn.win_findbuf(st_bufnr)) do
            patch_win(win)
          end
        end
      end
      -- Also patch any future window that enters an enabled buffer.
      vim.api.nvim_create_autocmd("BufWinEnter", {
        group = group,
        callback = function(args)
          local st = state_by_buf[args.buf]
          if st and st.enabled then
            local win = vim.fn.bufwinid(args.buf)
            if win ~= -1 then
              patch_win(win)
            end
          end
        end,
      })
    end,
  })
end

return M
