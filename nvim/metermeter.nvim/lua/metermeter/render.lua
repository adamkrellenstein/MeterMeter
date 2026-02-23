local M = {}

function M.render_signature(cache_write_seq, results)
  return tostring(cache_write_seq or 0) .. ":" .. tostring(results and #results or 0)
end

return M
