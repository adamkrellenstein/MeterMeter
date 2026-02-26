local config = require("metermeter.config")

local M = {}

function M.has_ft_token(bufnr, token)
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

function M.should_enable(bufnr)
  if not vim.api.nvim_buf_is_valid(bufnr) then
    return false
  end
  if vim.bo[bufnr].buftype ~= "" then
    return false
  end

  -- Modeline-friendly: user can opt-in via filetype.
  -- Example modeline: `vim: set ft=typst.metermeter :`
  return M.has_ft_token(bufnr, "metermeter")
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

function M.is_comment_line(bufnr, text)
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

function M.is_scan_line(bufnr, text)
  local s = vim.trim(text or "")
  if s == "" then
    return false
  end
  if M.is_comment_line(bufnr, s) then
    return false
  end
  local require_backslash = config.cfg.require_trailing_backslash and true or false
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

return M
