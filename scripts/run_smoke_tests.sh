#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[smoke] nvim plugin (backslash gating)"
nvim --headless -u NONE -i NONE \
  +"lua package.path = package.path .. ';${ROOT}/nvim/metermeter.nvim/lua/?.lua;${ROOT}/nvim/metermeter.nvim/lua/?/init.lua'" \
  +"luafile ${ROOT}/scripts/nvim_smoke_test.lua"

echo "[smoke] python unit tests (no network)"
python3 -m unittest discover -s tests -p 'test_nvim_*.py' -q
echo "[smoke] ok"
