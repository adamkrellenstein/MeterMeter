-- Auto-setup with defaults, and define user commands.
-- Users can call require("poetrymeter").setup({ ... }) to override defaults.

if vim.g.poetrymeter_disable_auto_setup ~= 1 then
  pcall(function()
    require("poetrymeter").setup()
  end)
end

vim.api.nvim_create_user_command("PoetryMeterEnable", function()
  require("poetrymeter").enable(0)
end, {})

vim.api.nvim_create_user_command("PoetryMeterDisable", function()
  require("poetrymeter").disable(0)
end, {})

vim.api.nvim_create_user_command("PoetryMeterToggle", function()
  require("poetrymeter").toggle(0)
end, {})

vim.api.nvim_create_user_command("PoetryMeterRescan", function()
  require("poetrymeter").rescan(0)
end, {})

vim.api.nvim_create_user_command("PoetryMeterDump", function()
  require("poetrymeter").dump_debug(0)
end, {})

