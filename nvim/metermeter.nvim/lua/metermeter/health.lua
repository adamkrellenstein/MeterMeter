local M = {}

function M.check()
  vim.health.start("metermeter")

  -- Neovim version
  if vim.fn.has("nvim-0.10") == 1 then
    vim.health.ok("Neovim >= 0.10")
  else
    vim.health.error("Neovim 0.10+ is required", { "Upgrade Neovim to 0.10 or later" })
  end

  -- Python version
  local plugin_root = vim.fn.fnamemodify(debug.getinfo(1, "S").source:sub(2), ":p:h:h:h")
  local project_root = vim.fn.fnamemodify(plugin_root, ":h:h")
  local venv_python = project_root .. "/.venv/bin/python3"
  local python = vim.fn.executable(venv_python) == 1 and venv_python or "python3"

  if vim.fn.executable(python) == 1 then
    local out = vim.fn.system({ python, "--version" })
    local maj, min = (out or ""):match("(%d+)%.(%d+)")
    maj = tonumber(maj)
    min = tonumber(min)
    if maj and min and (maj > 3 or (maj == 3 and min >= 11)) then
      vim.health.ok("Python " .. vim.trim(out))
    else
      vim.health.error("Python 3.11+ required, found: " .. vim.trim(out or "unknown"))
    end
  else
    vim.health.error("Python not found", { "Install Python 3.11+ and create a venv" })
  end

  -- prosodic importable
  local prosodic_check = vim.fn.system({ python, "-c", "import prosodic" })
  if vim.v.shell_error == 0 then
    vim.health.ok("prosodic is importable")
  else
    vim.health.error("prosodic is not importable", {
      "Install prosodic: uv pip install prosodic",
      vim.trim(prosodic_check or ""),
    })
  end

  -- espeak
  if vim.fn.executable("espeak") == 1 or vim.fn.executable("espeak-ng") == 1 then
    vim.health.ok("espeak found")
  else
    vim.health.warn("espeak not found (used as fallback for OOV words)", {
      "Install with: brew install espeak (macOS) or apt install espeak (Linux)",
    })
  end

  -- subprocess status
  local sub_ok, subprocess = pcall(require, "metermeter.subprocess")
  if sub_ok then
    if subprocess.is_running() then
      vim.health.ok("Subprocess is running")
    else
      vim.health.info("Subprocess is not running (starts on first scan)")
    end
  end

  -- setup() called (check for augroup)
  local augroup_ok = pcall(vim.api.nvim_get_autocmds, { group = "MeterMeter" })
  if augroup_ok then
    vim.health.ok("setup() has been called")
  else
    vim.health.warn("setup() has not been called", {
      'Call require("metermeter").setup() in your config',
    })
  end
end

return M
