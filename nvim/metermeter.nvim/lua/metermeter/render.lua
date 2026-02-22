local M = {}

function M.render_signature(encode_fn, results)
  local ok, encoded = pcall(encode_fn, results or {})
  if ok and type(encoded) == "string" then
    return encoded
  end
  return tostring(results and #results or 0)
end

return M
