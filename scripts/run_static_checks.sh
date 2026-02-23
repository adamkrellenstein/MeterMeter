#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[static] python compile checks"
python3 -m compileall -q "${ROOT}/nvim/metermeter.nvim/python" "${ROOT}/tests"

echo "[static] lua load checks (headless nvim)"
nvim --headless -u NONE -i NONE \
  +"lua package.path = package.path .. ';${ROOT}/nvim/metermeter.nvim/lua/?.lua;${ROOT}/nvim/metermeter.nvim/lua/?/init.lua'" \
  +"luafile ${ROOT}/nvim/metermeter.nvim/lua/metermeter/init.lua" \
  +"luafile ${ROOT}/nvim/metermeter.nvim/plugin/metermeter.lua" \
  +qa!

echo "[static] ok"
