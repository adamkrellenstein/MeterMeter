#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

"${ROOT}/scripts/run_static_checks.sh"
"${ROOT}/scripts/run_smoke_tests.sh"

if [[ "${METERMETER_LLM_INTEGRATION:-0}" == "1" ]]; then
  "${ROOT}/scripts/run_llm_integration_tests.sh"
fi
