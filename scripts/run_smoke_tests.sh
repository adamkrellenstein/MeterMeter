#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[smoke] nvim plugin (engine + backslash gating)"
nvim --headless -u NONE -i NONE \
  +"lua package.path = package.path .. ';${ROOT}/nvim/poetrymeter.nvim/lua/?.lua;${ROOT}/nvim/poetrymeter.nvim/lua/?/init.lua'" \
  +"luafile ${ROOT}/scripts/nvim_smoke_test.lua"

echo "[smoke] python unit tests (llm mocked; no network)"
python3 -m unittest -q tests.test_nvim_llm_refiner tests.test_nvim_cli_llm tests.test_nvim_stress_spans
echo "[smoke] ok"
