-- Manages a single persistent Python subprocess for the Neovim session.
-- Communication is newline-delimited JSON over stdin/stdout pipes.
-- Each request has an integer "id"; responses echo the id for callback matching.

local uv = vim.uv or vim.loop
local M = {}

local proc = nil
local stdin_pipe = nil
local stdout_pipe = nil
local stderr_pipe = nil
local pending = {}   -- id -> callback
local next_id = 1
local stdout_buf = ""
local stderr_buf = ""

local restart_count = 0
local restart_window_start = 0
local MAX_RESTARTS = 3
local RESTART_WINDOW_S = 60

local function _can_restart()
  local now = uv.now() / 1000
  if now - restart_window_start > RESTART_WINDOW_S then
    restart_count = 0
    restart_window_start = now
  end
  return restart_count < MAX_RESTARTS
end

local function _fail_all_pending(err)
  local cbs = pending
  pending = {}
  for _, cb in pairs(cbs) do
    vim.schedule(function() cb(nil, err) end)
  end
end

local function _on_stderr_data(data)
  if data then
    stderr_buf = stderr_buf .. data
  end
end

local function _on_stdout_data(data)
  if not data then return end
  stdout_buf = stdout_buf .. data
  while true do
    local nl = stdout_buf:find("\n", 1, true)
    if not nl then break end
    local line = stdout_buf:sub(1, nl - 1)
    stdout_buf = stdout_buf:sub(nl + 1)
    if line ~= "" then
      local ok, obj = pcall(vim.json.decode, line)
      if ok and type(obj) == "table" and obj.id then
        local cb = pending[obj.id]
        if cb then
          pending[obj.id] = nil
          vim.schedule(function() cb(obj, nil) end)
        end
      end
    end
  end
end

local function _on_exit(code, signal)
  proc = nil
  stdin_pipe = nil
  stdout_pipe = nil
  stderr_pipe = nil
  local err = "metermeter subprocess exited"
  if code and code ~= 0 then
    err = err .. " (code " .. tostring(code) .. ")"
  end
  if stderr_buf ~= "" then
    local short = stderr_buf:match("[%w]*Error[%w]*:[^\n]*") or stderr_buf:sub(1, 120)
    err = err .. ": " .. short
    stderr_buf = ""
  end
  stdout_buf = ""
  _fail_all_pending(err)
end

function M.ensure_running(cmd)
  if proc ~= nil then
    return true
  end
  if not _can_restart() then
    return false
  end
  restart_count = restart_count + 1

  stdin_pipe = uv.new_pipe(false)
  stdout_pipe = uv.new_pipe(false)
  stderr_pipe = uv.new_pipe(false)

  local handle, err = uv.spawn(cmd[1], {
    args = { unpack(cmd, 2) },
    stdio = { stdin_pipe, stdout_pipe, stderr_pipe },
  }, function(code, signal)
    vim.schedule(function() _on_exit(code, signal) end)
  end)

  if not handle then
    stdin_pipe:close()
    stdout_pipe:close()
    stderr_pipe:close()
    stdin_pipe = nil
    stdout_pipe = nil
    stderr_pipe = nil
    _fail_all_pending("failed to spawn metermeter subprocess: " .. tostring(err))
    return false
  end

  proc = handle

  uv.read_start(stdout_pipe, function(read_err, data)
    if read_err then return end
    _on_stdout_data(data)
  end)

  uv.read_start(stderr_pipe, function(read_err, data)
    if read_err then return end
    _on_stderr_data(data)
  end)

  return true
end

function M.send(request, callback)
  local id = next_id
  next_id = next_id + 1
  request.id = id
  pending[id] = callback

  local line = vim.json.encode(request) .. "\n"
  uv.write(stdin_pipe, line, function(write_err)
    if write_err then
      local cb = pending[id]
      if cb then
        pending[id] = nil
        vim.schedule(function() cb(nil, "subprocess write error: " .. tostring(write_err)) end)
      end
    end
  end)
end

function M.shutdown()
  if proc == nil then return end
  pcall(function()
    uv.write(stdin_pipe, vim.json.encode({ shutdown = true }) .. "\n")
  end)
  vim.defer_fn(function()
    if proc then
      pcall(uv.process_kill, proc, "sigterm")
    end
  end, 200)
  _fail_all_pending("shutting down")
end

function M.is_running()
  return proc ~= nil
end

function M.last_stderr()
  return stderr_buf
end

return M
