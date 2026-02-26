local M = {}

function M.candidate_line_set_for_buf(bufnr, prefetch)
  local wins = vim.fn.win_findbuf(bufnr)
  local visible = {}
  local prefetch_set = {}
  prefetch = tonumber(prefetch) or 0
  local line_count = vim.api.nvim_buf_line_count(bufnr)
  local max_row = math.max(0, line_count - 1)
  for _, win in ipairs(wins) do
    win = tonumber(win) or win
    if type(win) == "number" and vim.api.nvim_win_is_valid(win) then
      local w0, w1 = vim.api.nvim_win_call(win, function()
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

function M.combine_lines(visible_lines, prefetch_lines)
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

function M.all_scan_lines_for_buf(bufnr, is_scan_line)
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

function M.subtract_lines(base_lines, remove_lines)
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

function M.viewport_signature(bufnr)
  local wins = vim.fn.win_findbuf(bufnr)
  if type(wins) ~= "table" or #wins == 0 then
    return "no-win"
  end
  local parts = {}
  for _, win in ipairs(wins) do
    win = tonumber(win) or win
    if type(win) == "number" and vim.api.nvim_win_is_valid(win) then
      local w0, w1 = vim.api.nvim_win_call(win, function()
        return vim.fn.line("w0"), vim.fn.line("w$")
      end)
      local cur = vim.api.nvim_win_get_cursor(win)
      local ww = vim.api.nvim_win_get_width(win)
      table.insert(
        parts,
        table.concat({
          tostring(w0),
          tostring(w1),
          tostring(cur and cur[1] or 0),
          tostring(ww or 0),
        }, ":")
      )
    end
  end
  table.sort(parts)
  return table.concat(parts, "|")
end

return M
