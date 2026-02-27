local uv = vim.uv or vim.loop
local config = require("metermeter.config")
local highlight = require("metermeter.highlight")
local labels = require("metermeter.labels")

local M = {}

local ns = vim.api.nvim_create_namespace("metermeter")
local loading_ns = vim.api.nvim_create_namespace("metermeter_loading")
local SPINNER = { "⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏" }

-- Expose namespace for debug_dump extmark queries.
M.ns = ns

function M.clear_buf(bufnr)
  if not vim.api.nvim_buf_is_valid(bufnr) then
    return
  end
  vim.api.nvim_buf_clear_namespace(bufnr, ns, 0, -1)
  vim.api.nvim_buf_clear_namespace(bufnr, loading_ns, 0, -1)
end

local function _clear_loading(bufnr)
  if not vim.api.nvim_buf_is_valid(bufnr) then
    return
  end
  vim.api.nvim_buf_clear_namespace(bufnr, loading_ns, 0, -1)
end

function M.refresh_loading(bufnr, state_by_buf)
  if not vim.api.nvim_buf_is_valid(bufnr) then
    return
  end
  _clear_loading(bufnr)

  local st = state_by_buf[bufnr]
  if not st then
    return
  end
  st.spinner_frame = (st.spinner_frame + 1) % #SPINNER
  local spinner_char = SPINNER[st.spinner_frame + 1]

  -- Determine the column alignment based on the widest visible line
  local max_w = 0
  local line_count = vim.api.nvim_buf_line_count(bufnr)
  for _, lnum in ipairs(st.pending_lnums or {}) do
    if lnum >= 0 and lnum < line_count then
      local text = (vim.api.nvim_buf_get_lines(bufnr, lnum, lnum + 1, false)[1] or "")
      local w = vim.fn.strdisplaywidth(text)
      if type(w) == "number" and w > max_w then
        max_w = w
      end
    end
  end
  local eol_col = max_w + 1

  -- Clamp to window width
  local wins = vim.fn.win_findbuf(bufnr)
  if type(wins) == "table" then
    for _, win in ipairs(wins) do
      win = tonumber(win) or win
      if type(win) == "number" and vim.api.nvim_win_is_valid(win) then
        local ww = vim.api.nvim_win_get_width(win)
        if type(ww) == "number" and ww > 1 then
          eol_col = math.min(eol_col, ww - 1)
        end
        break
      end
    end
  end

  for _, lnum in ipairs(st.pending_lnums or {}) do
    vim.api.nvim_buf_set_extmark(bufnr, loading_ns, lnum, 0, {
      virt_text = { { " " .. spinner_char, "MeterMeterLoading" } },
      virt_text_pos = "overlay",
      virt_text_win_col = eol_col,
    })
  end

  -- Start the per-buffer spinner timer if not running
  if not st.loading_timer then
    st.loading_timer = uv.new_timer()
    st.loading_timer:start(100, 100, function()
      vim.schedule(function()
        if vim.api.nvim_buf_is_valid(bufnr) then
          local st2 = state_by_buf[bufnr]
          if st2 and #(st2.pending_lnums or {}) > 0 then
            M.refresh_loading(bufnr, state_by_buf)
          end
        end
      end)
    end)
  end
end

function M.stop_loading(bufnr, state_by_buf)
  local st = state_by_buf[bufnr]
  if st then
    if st.loading_timer then
      st.loading_timer:stop()
      st.loading_timer:close()
      st.loading_timer = nil
    end
    _clear_loading(bufnr)
  end
end

function M.apply_results(bufnr, results)
  M.clear_buf(bufnr)
  if not results then
    return
  end
  local cfg = config.cfg
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
      local conf = item.confidence
      local label = labels.meter_hint(item)
      if cfg.ui.meter_hints and label ~= "" then
        local hl = highlight.eol_hl_for_conf(conf)
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
end

return M
