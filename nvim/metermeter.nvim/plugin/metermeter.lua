-- Auto-setup with defaults, and define user commands.
-- Users can call require("metermeter").setup({ ... }) to override defaults.

if vim.g.metermeter_disable_auto_setup ~= 1 then
  pcall(function()
    require("metermeter").setup()
  end)
end

-- Commands
vim.api.nvim_create_user_command("MeterMeterToggle", function()
  require("metermeter").toggle(0)
end, {})

vim.api.nvim_create_user_command("MeterMeterEnable", function()
  require("metermeter").enable(0)
end, {})

vim.api.nvim_create_user_command("MeterMeterDisable", function()
  require("metermeter").disable(0)
end, {})

vim.api.nvim_create_user_command("MeterMeterRescan", function()
  require("metermeter").rescan(0)
end, {})

vim.api.nvim_create_user_command("MeterMeterDebug", function()
  require("metermeter").debug_dump(0)
end, {})

vim.api.nvim_create_user_command("MeterMeterStatus", function()
  local sl = require("metermeter").statusline(0)
  vim.notify(sl ~= "" and sl or "MeterMeter: not active", vim.log.levels.INFO)
end, {})

-- <Plug> mappings
vim.keymap.set("n", "<Plug>(metermeter-toggle)", function()
  require("metermeter").toggle(0)
end, { desc = "Toggle MeterMeter" })

vim.keymap.set("n", "<Plug>(metermeter-rescan)", function()
  require("metermeter").rescan(0)
end, { desc = "Rescan MeterMeter" })

vim.keymap.set("n", "<Plug>(metermeter-enable)", function()
  require("metermeter").enable(0)
end, { desc = "Enable MeterMeter" })

vim.keymap.set("n", "<Plug>(metermeter-disable)", function()
  require("metermeter").disable(0)
end, { desc = "Disable MeterMeter" })
