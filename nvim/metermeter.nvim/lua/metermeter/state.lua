local M = {}

function M.new_state()
  return {
    enabled = false,
    user_enabled = nil, -- nil => auto by filetype, bool => explicit buffer override
    timer = nil,
    tick = nil,
    cache = {},
    cache_size = 0,
    cache_seq = 0,
    cache_write_seq = 0,
    cache_epoch = 0,
    scan_generation = 0,
    scan_running = false,
    scan_changedtick = -1,
    last_changedtick = -1,
    last_view_sig = "",
    last_render_sig = "",
    last_llm_changedtick = -1,
    last_llm_view_sig = "",
    llm_fail_count = 0,
    llm_cooldown_until = 0,
    llm_error = "",
    dominant_meter = "",
    dominant_ratio = 0,
    dominant_line_count = 0,
    debug_scan_count = 0,
    debug_cli_count = 0,
    debug_llm_cli_count = 0,
    debug_apply_count = 0,
  }
end

function M.stop_scan_state(st)
  st.scan_running = false
  st.scan_changedtick = -1
  st.last_changedtick = -1
  st.last_view_sig = ""
end

return M
