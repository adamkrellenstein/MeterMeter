-- Auto-setup with defaults, and define user commands.
-- Users can call require("metermeter").setup({ ... }) to override defaults.

if vim.g.metermeter_disable_auto_setup ~= 1 then
  pcall(function()
    require("metermeter").setup()
  end)
end

vim.api.nvim_create_user_command("MeterMeterToggle", function()
  require("metermeter").toggle(0)
end, {})

vim.api.nvim_create_user_command("MeterMeterRescan", function()
  require("metermeter").rescan(0)
end, {})

vim.api.nvim_create_user_command("MeterMeterDump", function()
  require("metermeter").dump_debug(0)
end, {})
