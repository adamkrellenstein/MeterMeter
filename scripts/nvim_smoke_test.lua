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

local function loading_marks(bufnr)
  local ns = vim.api.nvim_get_namespaces()["metermeter_loading"]
  if not ns then
    return {}
  end
  return vim.api.nvim_buf_get_extmarks(bufnr, ns, 0, -1, { details = true })
end

local function run_backslash_gate()
  metermeter.setup({
    rescan_interval_ms = 0,
    debounce_ms = 1,
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

  metermeter.disable(bufnr)
  vim.wait(100)
end

local function run_comment_ignore()
  metermeter.setup({
    rescan_interval_ms = 0,
    debounce_ms = 1,
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

  metermeter.disable(bufnr)
  vim.wait(100)
end

local function run_filetype_token_enable()
  metermeter.setup({
    rescan_interval_ms = 0,
    debounce_ms = 1,
    require_trailing_backslash = false,
  })

  vim.wait(100)
  vim.cmd("enew")
  local bufnr = vim.api.nvim_get_current_buf()
  vim.api.nvim_buf_set_name(bufnr, "/tmp/metermeter_smoke_token.txt")
  -- Modeline-style enable path: filetype includes "metermeter" token.
  vim.bo[bufnr].filetype = "typst.metermeter"
  vim.api.nvim_buf_set_lines(bufnr, 0, -1, false, {
    "A line that should be annotated via filetype token.",
  })

  metermeter.enable(bufnr)
  local ok = wait_for(function()
    return #extmarks(bufnr) > 0
  end, 8000)
  if not ok then
    fail("filetype token: no extmarks created")
  end

  metermeter.disable(bufnr)
  vim.wait(100)
end

local function run_duplicate_lines_cache_binding()
  metermeter.setup({
    rescan_interval_ms = 0,
    debounce_ms = 1,
    require_trailing_backslash = false,
  })

  vim.cmd("enew")
  local bufnr = vim.api.nvim_get_current_buf()
  vim.api.nvim_buf_set_name(bufnr, "/tmp/metermeter_smoke_dupes.poem")
  vim.bo[bufnr].filetype = "metermeter"
  vim.api.nvim_buf_set_lines(bufnr, 0, -1, false, {
    "The trampled fruit yields wine that's sweet and red.",
    "And plants will dream, thy flax to fit a nuptial bed.",
    "The trampled fruit yields wine that's sweet and red.",
    "And plants will dream, thy flax to fit a nuptial bed.",
  })

  metermeter.enable(bufnr)
  local ok = wait_for(function()
    local marks = extmarks(bufnr)
    local eol = 0
    for _, m in ipairs(marks) do
      local d = m[4] or {}
      if d.virt_text then
        eol = eol + 1
      end
    end
    return eol >= 4
  end, 4000)
  if not ok then
    fail("duplicate lines: expected meter marks on all repeated lines")
  end

  metermeter.disable(bufnr)
  vim.wait(100)
end

local function run_manual_toggle_for_non_poem()
  metermeter.setup({
    rescan_interval_ms = 0,
    debounce_ms = 1,
    require_trailing_backslash = false,
  })

  vim.cmd("enew")
  local bufnr = vim.api.nvim_get_current_buf()
  vim.api.nvim_buf_set_name(bufnr, "/tmp/metermeter_smoke_attach.typ")
  vim.bo[bufnr].filetype = "typst"
  vim.api.nvim_buf_set_lines(bufnr, 0, -1, false, {
    "The trampled fruit yields wine that's sweet and red.",
  })

  -- Should not auto-enable for plain typst.
  vim.wait(50)
  if #extmarks(bufnr) ~= 0 then
    fail("manual attach: unexpected marks before attach")
  end

  metermeter.toggle(bufnr)
  local ok = wait_for(function()
    return #extmarks(bufnr) > 0
  end, 4000)
  if not ok then
    fail("manual toggle: no extmarks created after toggle on")
  end

  metermeter.toggle(bufnr)
  vim.wait(50)
  if #extmarks(bufnr) ~= 0 then
    fail("manual toggle: extmarks remain after toggle off")
  end

  vim.wait(100)
end

local function run_idle_no_extra_work()
  metermeter.setup({
    rescan_interval_ms = 100,
    debounce_ms = 1,
    require_trailing_backslash = false,
  })

  vim.cmd("enew")
  local bufnr = vim.api.nvim_get_current_buf()
  vim.api.nvim_buf_set_name(bufnr, "/tmp/metermeter_smoke_idle.poem")
  vim.bo[bufnr].filetype = "metermeter"
  vim.api.nvim_buf_set_lines(bufnr, 0, -1, false, {
    "The trampled fruit yields wine that's sweet and red.",
    "And plants will dream, thy flax to fit a nuptial bed.",
  })

  metermeter.enable(bufnr)
  local ok = wait_for(function()
    local s = metermeter._debug_stats(bufnr)
    return s.apply_count > 0 and #extmarks(bufnr) > 0
  end, 4000)
  if not ok then
    fail("idle check: initial scan did not complete")
  end

  local settled = wait_for(function()
    local s = metermeter._debug_stats(bufnr)
    return not s.scan_running
  end, 4000)
  if not settled then
    fail("idle check: scan pipeline did not settle")
  end

  local before = metermeter._debug_stats(bufnr)
  vim.wait(450)
  local after = metermeter._debug_stats(bufnr)

  if after.cli_count ~= before.cli_count then
    fail("idle check: cli work increased during idle")
  end
  if after.apply_count ~= before.apply_count then
    fail("idle check: redraw/apply increased during idle")
  end

  metermeter.disable(bufnr)
  vim.wait(100)
end

local function run_confidence_shading()
  metermeter.setup({
    rescan_interval_ms = 0,
    debounce_ms = 1,
    require_trailing_backslash = false,
  })

  vim.cmd("enew")
  local bufnr = vim.api.nvim_get_current_buf()
  vim.api.nvim_buf_set_name(bufnr, "/tmp/metermeter_smoke_confidence.poem")
  vim.bo[bufnr].filetype = "metermeter"
  vim.api.nvim_buf_set_lines(bufnr, 0, -1, false, {
    "Shall I compare thee to a summer's day?",
    "One ring to rule them all",
  })

  metermeter.enable(bufnr)
  local ok = wait_for(function()
    local marks = extmarks(bufnr)
    local has_marks = #marks >= 2
    if has_marks then
      local has_eol2 = false
      for _, m in ipairs(marks) do
        local d = m[4] or {}
        if d.virt_text and d.virt_text[1][2] == "MeterMeterEOL1" then
          has_eol2 = true
          break
        end
      end
      return has_eol2
    end
    return false
  end, 4000)
  if not ok then
    fail("confidence shading: should have marks including at least one confident tier")
  end

  metermeter.disable(bufnr)
  vim.wait(100)
end

local function run_loading_indicator()
  metermeter.setup({
    rescan_interval_ms = 0,
    debounce_ms = 1,
    require_trailing_backslash = false,
    ui = {
      stress = true,
      meter_hints = true,
      loading_indicator = true,
    },
  })

  vim.cmd("enew")
  local bufnr = vim.api.nvim_get_current_buf()
  vim.api.nvim_buf_set_name(bufnr, "/tmp/metermeter_smoke_loading.poem")
  vim.bo[bufnr].filetype = "metermeter"
  vim.api.nvim_buf_set_lines(bufnr, 0, -1, false, {
    "The trampled fruit yields wine that's sweet and red.",
  })

  metermeter.enable(bufnr)

  local saw_loading = false
  local has_results = false
  local ok = wait_for(function()
    -- Check for loading marks
    if #loading_marks(bufnr) > 0 then
      saw_loading = true
    end
    -- Check for result marks
    if #extmarks(bufnr) > 0 then
      has_results = true
    end
    -- Return true only when we saw loading marks AND results are present
    return saw_loading and has_results
  end, 4000)

  if not saw_loading then
    fail("loading indicator: did not observe loading marks")
  end
  if not has_results then
    fail("loading indicator: no results appeared")
  end

  -- After completion, loading marks should be cleared
  vim.wait(100)
  if #loading_marks(bufnr) ~= 0 then
    fail("loading indicator: loading marks should be cleared after completion")
  end

  metermeter.disable(bufnr)
  vim.wait(100)
end

local function run_loading_indicator_duplicate_background()
  -- Regression: when many background lines are duplicates of visible lines, they can be satisfied via cache
  -- without a CLI request; ensure pending bookkeeping clears spinners anyway.
  metermeter.setup({
    rescan_interval_ms = 0,
    debounce_ms = 1,
    prefetch_lines = 0,
    require_trailing_backslash = false,
    ui = {
      stress = true,
      meter_hints = true,
      loading_indicator = true,
    },
  })

  vim.cmd("enew")
  local bufnr = vim.api.nvim_get_current_buf()
  vim.api.nvim_buf_set_name(bufnr, "/tmp/metermeter_smoke_loading_dupes.poem")
  vim.bo[bufnr].filetype = "metermeter"

  local lines = {}
  for _ = 1, 120 do
    lines[#lines + 1] = "The trampled fruit yields wine that's sweet and red."
  end
  vim.api.nvim_buf_set_lines(bufnr, 0, -1, false, lines)

  metermeter.enable(bufnr)

  local saw_loading = false
  local ok = wait_for(function()
    if #loading_marks(bufnr) > 0 then
      saw_loading = true
    end
    local s = metermeter._debug_stats(bufnr)
    return saw_loading and (not s.scan_running) and (#extmarks(bufnr) > 0)
  end, 8000)
  if not ok then
    fail("loading dupes: expected loading marks and settled scan with results")
  end

  vim.wait(150)
  if #loading_marks(bufnr) ~= 0 then
    fail("loading dupes: loading marks should be cleared after completion")
  end

  metermeter.disable(bufnr)
  vim.wait(100)
end

local function main()
  run_backslash_gate()
  run_comment_ignore()
  run_filetype_token_enable()
  run_duplicate_lines_cache_binding()
  run_manual_toggle_for_non_poem()
  run_idle_no_extra_work()
  run_confidence_shading()
  run_loading_indicator_duplicate_background()
  run_loading_indicator()
  vim.cmd("qa!")
end

main()
