-- Neovim headless smoke test for poetrymeter.nvim.

local function fail(msg)
  vim.api.nvim_err_writeln(msg)
  vim.cmd("cq")
end

local plugin_dir = vim.fn.getcwd() .. "/nvim/poetrymeter.nvim"
vim.opt.runtimepath:prepend(plugin_dir)

-- Avoid filesystem differences causing swap failures in headless environments.
vim.opt.swapfile = false

vim.g.poetrymeter_disable_auto_setup = 1

local poetrymeter = require("poetrymeter")

local function wait_for(pred, timeout_ms)
  local ok = vim.wait(timeout_ms, function()
    return pred()
  end, 25)
  return ok
end

local function extmarks(bufnr)
  local ns = vim.api.nvim_get_namespaces()["poetrymeter"]
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
    if d.hl_group == "PoetryMeterStress" then
      n = n + 1
    end
  end
  return n
end

local function run_poem_engine_only()
  poetrymeter.setup({
    enabled_by_default = false,
    rescan_interval_ms = 0,
    debounce_ms = 1,
    llm = { enabled = false },
  })

  vim.cmd("enew")
  local bufnr = vim.api.nvim_get_current_buf()
  vim.api.nvim_buf_set_name(bufnr, "/tmp/poetrymeter_smoke.poem")
  vim.api.nvim_buf_set_lines(bufnr, 0, -1, false, {
    "The trampled fruit yields wine that's sweet and red.",
    "And plants will dream, thy flax to fit a nuptial bed.",
  })

  poetrymeter.enable(bufnr)

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

local function run_typst_opt_in()
  poetrymeter.setup({
    enabled_by_default = false,
    rescan_interval_ms = 0,
    debounce_ms = 1,
    llm = { enabled = false },
    enabled_file_extensions = { ".poem" },
    opt_in_file_extensions = { ".typ" },
    opt_in_marker = "poetrymeter: on",
  })

  vim.cmd("enew")
  local bufnr = vim.api.nvim_get_current_buf()
  vim.api.nvim_buf_set_name(bufnr, "/tmp/poetrymeter_smoke.typ")
  vim.api.nvim_buf_set_lines(bufnr, 0, -1, false, {
    "// poetrymeter: on",
    '#import "style/template.typ": poem, stanza, couplet',
    "",
    "#stanza[",
    "  Such trampled fruit yields wine that's sweet and red; \\",
    "]",
  })

  poetrymeter.enable(bufnr)
  local ok = wait_for(function()
    return #extmarks(bufnr) > 0
  end, 4000)
  if not ok then
    fail("typst opt-in: no extmarks created")
  end

  local marks = extmarks(bufnr)
  for _, m in ipairs(marks) do
    local row = m[2]
    -- Should not annotate the marker/import lines (0..2). Poem content is row 4.
    if row < 3 then
      fail("typst opt-in: annotated outside stanza block (row=" .. tostring(row) .. ")")
    end
  end
end

local function main()
  run_poem_engine_only()
  run_typst_opt_in()
  vim.cmd("qa!")
end

main()
