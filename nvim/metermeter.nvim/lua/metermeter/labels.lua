local config = require("metermeter.config")

local M = {}

local FOOT_ABBREV = {
  iambic = "i",
  trochaic = "t",
  anapestic = "a",
  dactylic = "d",
}

local LINE_ABBREV = {
  monometer = "1",
  dimeter = "2",
  trimeter = "3",
  tetrameter = "4",
  pentameter = "P",
  hexameter = "H",
}

local function _details_mode()
  local mode = config.cfg and config.cfg.ui and config.cfg.ui.meter_hint_details
  if mode == "off" or mode == "always" or mode == "deviations" then
    return mode
  end
  return "deviations"
end

local function _abbrev_enabled()
  return config.cfg and config.cfg.ui and config.cfg.ui.meter_hint_abbrev == true
end

---@param meter_name string
---@return string
function M.abbrev_meter_name(meter_name)
  local foot, line = tostring(meter_name or ""):match("^%s*(%S+)%s+(%S+)%s*$")
  if not foot or not line then
    return tostring(meter_name or "")
  end
  foot = foot:lower()
  line = line:lower()
  local foot_abbrev = FOOT_ABBREV[foot]
  local line_abbrev = LINE_ABBREV[line]
  if foot_abbrev and line_abbrev then
    return foot_abbrev .. line_abbrev
  end
  return tostring(meter_name or "")
end

---@param item table
---@return string
function M.meter_hint(item)
  local meter_name = tostring(item and item.meter_name or "")
  if meter_name == "" then
    return ""
  end
  if _abbrev_enabled() then
    meter_name = M.abbrev_meter_name(meter_name)
  end

  local mode = _details_mode()
  if mode == "off" then
    return meter_name
  end

  local feats = item and item.meter_features
  if type(feats) ~= "table" then
    return meter_name
  end

  local notes = {}

  local ending = tostring(feats.ending or "")
  if mode == "always" then
    if ending == "masc" or ending == "fem" then
      notes[#notes + 1] = ending
    end
  else
    if ending == "fem" then
      notes[#notes + 1] = "fem"
    end
  end

  if feats.inversion then
    notes[#notes + 1] = "inv"
  end
  if feats.spondee then
    notes[#notes + 1] = "spon"
  end
  if feats.pyrrhic then
    notes[#notes + 1] = "pyrr"
  end

  if #notes == 0 then
    return meter_name
  end

  return meter_name .. " (" .. table.concat(notes, ", ") .. ")"
end

return M
