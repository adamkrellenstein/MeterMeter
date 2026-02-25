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
    dominant_meter = "",
    dominant_strength = 0,
    last_error = nil, -- string or nil; most recent CLI error message
    debug_scan_count = 0,
    debug_cli_count = 0,
    debug_apply_count = 0,
    pending_lnums = {},
    spinner_frame = 0,
    loading_timer = nil,
  }
end

function M.stop_scan_state(st)
  st.scan_running = false
  st.scan_changedtick = -1
  st.last_changedtick = -1
  st.last_view_sig = ""
  if st.loading_timer then
    st.loading_timer:stop()
    st.loading_timer:close()
    st.loading_timer = nil
  end
end

return M
