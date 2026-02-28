#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export PYTHONHASHSEED="${PYTHONHASHSEED:-0}"

PYTHON="${ROOT}/.venv/bin/python3"
if [[ ! -x "${PYTHON}" ]]; then
  PYTHON="python3"
fi

echo "[check] python compile"
"${PYTHON}" -m compileall -q "${ROOT}/nvim/metermeter.nvim/python" "${ROOT}/tests"

echo "[check] lua load"
nvim --headless -u NONE -i NONE \
  +"lua package.path = package.path .. ';${ROOT}/nvim/metermeter.nvim/lua/?.lua;${ROOT}/nvim/metermeter.nvim/lua/?/init.lua'" \
  +"luafile ${ROOT}/nvim/metermeter.nvim/lua/metermeter/init.lua" \
  +"luafile ${ROOT}/nvim/metermeter.nvim/plugin/metermeter.lua" \
  +qa!

echo "[test] unit tests"
cd "${ROOT}" && uv run pytest tests/ -q

echo "[test] nvim smoke tests"
cd "${ROOT}" && nvim --headless -u "${ROOT}/scripts/nvim_smoke_test.lua" +qa

echo "all checks passed"
