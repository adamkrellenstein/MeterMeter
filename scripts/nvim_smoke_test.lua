-- Neovim headless smoke test for metermeter.nvim.

local function fail(msg)
  vim.api.nvim_err_writeln(msg)
  vim.cmd("cq")
end

local plugin_dir = vim.fn.getcwd() .. "/nvim/metermeter.nvim"
vim.opt.runtimepath:prepend(plugin_dir)

-- Avoid filesystem differences causing swap failures in headless environments.
vim.opt.swapfile = false

vim.g.metermeter_disable_auto_setup = 1

local metermeter = require("metermeter")

local function wait_for(pred, timeout_ms)
  local ok = vim.wait(timeout_ms, function()
    return pred()
  end, 25)
  return ok
end

local function extmarks(bufnr)
  local ns = vim.api.nvim_get_namespaces()["metermeter"]
  if not ns then
    return {}
  end
  return vim.api.nvim_buf_get_extmarks(bufnr, ns, 0, -1, { details = true })
end

local function count_eol_marks(marks)
  local n = 0
  for _, m in ipairs(marks) do
    local d = m[4] or {}
    if d.virt_text then
      n = n + 1
    end
  end
  return n
end

local function count_hl_marks(marks)
  local n = 0
  for _, m in ipairs(marks) do
    local d = m[4] or {}
    if d.hl_group == "MeterMeterStress" then
      n = n + 1
    end
  end
  return n
end

local function run_poem_engine_only()
  metermeter.setup({
    enabled_by_default = false,
    rescan_interval_ms = 0,
    debounce_ms = 1,
    llm = { enabled = false },
    require_trailing_backslash = false,
  })

  vim.cmd("enew")
  local bufnr = vim.api.nvim_get_current_buf()
  vim.api.nvim_buf_set_name(bufnr, "/tmp/metermeter_smoke.poem")
  vim.bo[bufnr].filetype = "metermeter"
  vim.api.nvim_buf_set_lines(bufnr, 0, -1, false, {
    "The trampled fruit yields wine that's sweet and red.",
    "And plants will dream, thy flax to fit a nuptial bed.",
  })

  metermeter.enable(bufnr)

  local ok = wait_for(function()
    return #extmarks(bufnr) > 0
  end, 4000)
  if not ok then
    fail("poem engine-only: no extmarks created")
  end

  local marks = extmarks(bufnr)
  if count_eol_marks(marks) < 1 then
    fail("poem engine-only: expected at least one EOL virt_text mark")
  end
  if count_hl_marks(marks) < 1 then
    fail("poem engine-only: expected at least one stress highlight mark")
  end
end

local function run_backslash_gate()
  metermeter.setup({
    enabled_by_default = false,
    rescan_interval_ms = 0,
    debounce_ms = 1,
    llm = { enabled = false },
    require_trailing_backslash = true,
  })

  vim.cmd("enew")
  local bufnr = vim.api.nvim_get_current_buf()
  vim.api.nvim_buf_set_name(bufnr, "/tmp/metermeter_smoke_gate.poem")
  vim.bo[bufnr].filetype = "metermeter"
  vim.api.nvim_buf_set_lines(bufnr, 0, -1, false, {
    "This line should be ignored.",
    "This line should be annotated. \\",
    "Another ignored line.",
    "Another annotated line. \\",
  })

  metermeter.enable(bufnr)
  local ok = wait_for(function()
    return #extmarks(bufnr) > 0
  end, 4000)
  if not ok then
    fail("backslash gate: no extmarks created")
  end

  local marks = extmarks(bufnr)
  for _, m in ipairs(marks) do
    local row = m[2]
    if row == 0 or row == 2 then
      fail("backslash gate: annotated a non-\\\\ line (row=" .. tostring(row) .. ")")
    end
  end
end

local function run_comment_ignore()
  metermeter.setup({
    enabled_by_default = false,
    rescan_interval_ms = 0,
    debounce_ms = 1,
    llm = { enabled = false },
    require_trailing_backslash = false,
  })

  vim.cmd("enew")
  local bufnr = vim.api.nvim_get_current_buf()
  vim.api.nvim_buf_set_name(bufnr, "/tmp/metermeter_smoke_comments.poem")
  vim.bo[bufnr].filetype = "metermeter"
  -- Provide "native" comment hints via options (as filetypes normally do).
  vim.bo[bufnr].comments = "://,b:#"
  vim.bo[bufnr].commentstring = "// %s"

  vim.api.nvim_buf_set_lines(bufnr, 0, -1, false, {
    "// comment should be ignored",
    "# comment should be ignored",
    "This line should be annotated.",
  })

  metermeter.enable(bufnr)
  local ok = wait_for(function()
    return #extmarks(bufnr) > 0
  end, 4000)
  if not ok then
    fail("comment ignore: no extmarks created")
  end

  local marks = extmarks(bufnr)
  for _, m in ipairs(marks) do
    local row = m[2]
    if row == 0 or row == 1 then
      fail("comment ignore: annotated a comment line (row=" .. tostring(row) .. ")")
    end
  end
end

local function main()
  run_poem_engine_only()
  run_backslash_gate()
  run_comment_ignore()
  vim.cmd("qa!")
end

main()
