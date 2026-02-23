#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

: "${METERMETER_LLM_INTEGRATION:=1}"
export METERMETER_LLM_INTEGRATION
: "${METERMETER_LLM_PROGRESS:=1}"
export METERMETER_LLM_PROGRESS

echo "[llm-integration] real endpoint/model accuracy checks"
python3 -m unittest -q tests.test_nvim_llm_integration
if [[ "${METERMETER_LLM_EXTENDED:-0}" == "1" ]]; then
  echo "[llm-integration] extended corpus checks"
  python3 -m unittest -q tests.test_nvim_llm_integration_extended
fi
echo "[llm-integration] ok"
