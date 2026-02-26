local config = require("metermeter.config")

local M = {}

function M.compute_stress_hl()
  -- Bold overlay so it stays visible even when Normal bg is pure black.
  vim.api.nvim_set_hl(0, "MeterMeterStress", { bold = true })
  vim.api.nvim_set_hl(0, "MeterMeterEOL", { link = "Comment" })
  vim.api.nvim_set_hl(0, "MeterMeterLoading", { link = "Comment" })
end

function M._blend_rgb(a, b, t)
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

function M.compute_eol_hls()
  -- Two-tier confidence system: dim (low-confidence guess) and bright (confident).
  local normal = vim.api.nvim_get_hl(0, { name = "Normal", link = true }) or {}
  local comment = vim.api.nvim_get_hl(0, { name = "Comment", link = true }) or {}
  local nfg = normal.fg or normal.foreground
  local nbg = normal.bg or normal.background
  local cfgc = comment.fg or comment.foreground

  -- If we don't have truecolor info, fall back to cterm greys.
  if type(nfg) ~= "number" or type(nbg) ~= "number" or type(cfgc) ~= "number" then
    local dark = (vim.o.background or ""):lower() ~= "light"
    -- Tier 0: extra dim (closer to background), Tier 1: bright (normal fg)
    local dark_indices = { 238, 254 }
    local light_indices = { 253, 238 }
    local indices = dark and dark_indices or light_indices
    for i = 0, 1 do
      vim.api.nvim_set_hl(0, "MeterMeterEOL" .. tostring(i), { ctermfg = indices[i + 1] })
    end
    return
  end

  -- Tier 0: blend 20% from comment toward bg (dimmer than comment).
  -- Tier 1: full normal fg (bright).
  local dim_fg = M._blend_rgb(cfgc, nbg, 0.3)
  vim.api.nvim_set_hl(0, "MeterMeterEOL0", { fg = dim_fg })
  vim.api.nvim_set_hl(0, "MeterMeterEOL1", { fg = nfg })
end

function M.eol_hl_for_conf(conf)
  if type(conf) ~= "number" then
    return "MeterMeterEOL0"
  end
  if conf >= (config.cfg.ui.confident_threshold or 0.7) then
    return "MeterMeterEOL1"
  end
  return "MeterMeterEOL0"
end

return M
