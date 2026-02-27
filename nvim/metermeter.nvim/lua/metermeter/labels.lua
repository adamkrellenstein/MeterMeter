local config = require("metermeter.config")

local M = {}

local function _details_mode()
  local mode = config.cfg and config.cfg.ui and config.cfg.ui.meter_hint_details
  if mode == "off" or mode == "always" or mode == "deviations" then
    return mode
  end
  return "deviations"
end

---@param item table
---@return string
function M.meter_hint(item)
  local meter_name = tostring(item and item.meter_name or "")
  if meter_name == "" then
    return ""
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
