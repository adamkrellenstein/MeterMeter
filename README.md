# MeterMeter

Local, real-time poetic meter annotation for Neovim.

This repository is Neovim-first. Plugin code lives in `nvim/metermeter.nvim/`.

Scanning is progressive and incremental: visible lines are annotated first, nearby prefetch lines next, then remaining scannable lines across the buffer. Cached results are reused for unchanged lines.

## Architecture

Three layers:

**Neovim runtime** (`nvim/metermeter.nvim/lua/metermeter/`)
- Determines which lines are scannable (ignores native comment lines).
- Schedules scans with debounce and periodic tick.
- Runs progressive scan phases: `visible → prefetch → rest-of-buffer`.
- Maintains a per-buffer LRU cache keyed by `(model, endpoint, eval_mode, temperature, cache_epoch, line_text)`.
- Renders stress extmarks and aligned end-of-line meter hints.

**Python CLI** (`nvim/metermeter.nvim/python/metermeter_cli.py`)
- Bridges Neovim and the analysis engines via a stdin/stdout JSON protocol.
- Runs `MeterEngine` on each line to produce a deterministic baseline.
- Passes baselines to `LLMRefiner` for LLM-based refinement.
- Applies post-LLM overrides (pattern rescore, iambic guard, baseline guard, dominant-meter smoothing) in priority order; first match wins.
- Computes stress-highlight byte spans from syllable positions.

**Python analysis** (`nvim/metermeter.nvim/python/metermeter/`)
- `meter_engine.py`: wraps the `prosodic` OT metrical parser. Produces a `LineAnalysis` with per-syllable stress positions, per-token patterns, meter name, foot count, and confidence.
- `llm_refiner.py`: sends baseline analyses to the LLM, then validates and repairs the response (meter name canonicalization, token pattern length repair, confidence clamping).

### Pipeline

```
line text
  → MeterEngine (prosodic)   → baseline: stress_pattern, meter_name, syllable_positions
  → LLMRefiner               → refined: meter_name, confidence, token_patterns
  → override checks          → final: meter_name, stress spans
  → Neovim extmarks
```

The LLM receives the baseline context and refines meter labels and per-token stress patterns. Post-LLM overrides catch cases where the LLM drifts from what the stress pattern actually supports.

If the LLM is unavailable or fails, no annotations are shown for that scan.

### Deterministic baseline accuracy

On the For Better For Verse (4B4V) annotated corpus (1,181 lines, 85 poems):

| Metric | Score |
|--------|-------|
| Meter classification | 71% |
| Per-syllable stress | 66% |
| Iambic pentameter stress F1 | 68% |

The main sources of error are archaic spellings that `prosodic`'s phoneme model syllabifies differently from the corpus annotators, and lines with first-foot inversions (trochaic substitution in an iambic poem), which a single-line meter classifier cannot resolve without poem context. The LLM layer addresses both by having access to the surrounding poem.

## Requirements

- Neovim 0.10+
- Python 3.11+ (`python3` on PATH)
- [`prosodic`](https://github.com/quadrismegistus/prosodic) Python package (installed in the venv)
- `espeak` system package (used by prosodic for out-of-vocabulary words; install with `brew install espeak` or `apt install espeak`)
- A local Ollama instance (or another OpenAI-compatible `chat/completions` endpoint) with a working model

## Install (lazy.nvim)

```lua
{
  dir = "~/dev/MeterMeter/nvim/metermeter.nvim",
  config = function()
    require("metermeter").setup({
      llm = {
        enabled = true,
        endpoint = "http://127.0.0.1:11434/v1/chat/completions",
        model = "qwen2.5:7b-instruct",
        timeout_ms = 30000,
      },
    })
  end,
}
```

## Install (vim-plug)

```vim
Plug '~/dev/MeterMeter/nvim/metermeter.nvim'
```

Then `:PlugInstall`. Optional (recommended):

```vim
set termguicolors
```

## Usage

- `:MeterMeterToggle`
- `:MeterMeterRescan`
- `:MeterMeterDump` (writes to `debug_dump_path`)
- `:MeterMeterStatus` (shows enable state, filetype match, LLM cooldown)

### Statusline

`require("metermeter").statusline()` returns:

- `""` when inactive
- `"MM: iambic pentameter"` (or dominant meter)
- `"MM: scanning"` during a scan
- `"MM: <error>"` on LLM failure (truncated to 60 chars)

```lua
require("lualine").setup({
  sections = {
    lualine_x = { function() return require("metermeter").statusline() end },
  },
})
```

## File Enable Rules

MeterMeter enables itself when `&filetype` includes `metermeter`.

- `*.poem` is detected automatically via `ftdetect`.
- Mixed-format files can opt-in via modeline:

```text
vim: set ft=typst.metermeter :
```

For ad hoc annotation of non-`.poem` files, use `:MeterMeterToggle`.

To restrict annotation to explicit poetry lines in mixed prose/code files:

```lua
require("metermeter").setup({ require_trailing_backslash = true })
```

Or without changing setup:

```vim
let b:metermeter_require_trailing_backslash = 1
```

Lines ending with `\` are then the only ones annotated. Comment lines (per `&comments`/`&commentstring`) are always ignored.

## Configuration Reference

```lua
require("metermeter").setup({ ... })
```

### Core options

| Option | Default | Description |
|--------|---------|-------------|
| `debounce_ms` | `80` | Delay after edits before rescanning. |
| `rescan_interval_ms` | `0` | Periodic rescan interval; `0` disables. |
| `prefetch_lines` | `5` | Lines scanned around cursor beyond visible range. |
| `require_trailing_backslash` | `false` | Only annotate lines ending with `\`. |
| `ui.stress` | `true` | Enable stress span highlighting. |
| `ui.meter_hints` | `true` | Show meter label at end of line. |
| `ui.meter_hint_confidence_levels` | `6` | Number of confidence tint steps (range 2–12). |
| `ui.show_error_hint` | `true` | Show inline hint when LLM fails. |
| `llm.enabled` | `true` | Enable LLM analysis. Must be `true` for annotations. |
| `llm.endpoint` | `http://127.0.0.1:11434/v1/chat/completions` | OpenAI-compatible chat endpoint. |
| `llm.model` | `qwen2.5:7b-instruct` | Model name sent to endpoint. |
| `llm.timeout_ms` | `30000` | HTTP timeout for LLM calls. |
| `llm.temperature` | `0.1` | Sampling temperature. |
| `llm.api_key` | `""` | Optional Bearer token. |
| `llm.eval_mode` | `"production"` | `"production"` enables normalization/repair; `"strict"` measures raw model output. |
| `llm.max_concurrent` | `1` | Concurrent LLM requests per scan phase. |

### Advanced options

| Option | Default | Description |
|--------|---------|-------------|
| `cache.max_entries` | `5000` | Per-buffer LRU cache capacity. |
| `debug_dump_path` | `"/tmp/metermeter_nvim_dump.json"` | Output path for `:MeterMeterDump`. |
| `llm.max_lines_per_scan` | `2` | Lines per LLM batch. |
| `llm.failure_threshold` | `3` | Consecutive failures before cooldown. |
| `llm.cooldown_ms` | `15000` | Cooldown duration after threshold. |

### Global variable

| Variable | Default | Description |
|----------|---------|-------------|
| `vim.g.metermeter_disable_auto_setup` | `0` | Set to `1` to suppress automatic `setup()` call. |

## Testing

```bash
uv run pytest tests/ -q --ignore=tests/test_nvim_llm_integration.py
```

Tests are in `tests/test_nvim_*.py`. The suite covers:

- Stress span correctness and multi-byte UTF-8 byte-index mapping
- LLM response parsing, validation, and repair
- Post-LLM override logic (pattern rescore, iambic guard, baseline guard, dominant smoothing)
- CLI command handling and error cases

**Corpus accuracy test** (runs 14 lines by default; slow):

```bash
# Quick smoke (14 lines, ~4s, accuracy assertions skipped as statistically meaningless)
uv run pytest tests/test_nvim_stress_accuracy.py

# Full 4B4V corpus (1,181 lines, ~30s)
MM_TEST_MAX_LINES=0 uv run pytest tests/test_nvim_stress_accuracy.py -v -s
```

**Benchmark** (requires 4B4V corpus in `benchmarks/data/poems/`):

```bash
# Deterministic baseline only
uv run python benchmarks/run_benchmark.py --no-llm --progress

# Full pipeline (requires running LLM endpoint)
uv run python benchmarks/run_benchmark.py --progress
```

Override LLM settings via environment:

```bash
METERMETER_LLM_ENDPOINT=http://... METERMETER_LLM_MODEL=llama3 uv run python benchmarks/run_benchmark.py
```

**LLM integration tests** (skipped automatically if endpoint unreachable):

```bash
./scripts/run_llm_integration_tests.sh
```

**Neovim smoke test** (requires `nvim` on PATH):

```bash
./scripts/run_smoke_tests.sh
```

## CLI JSON Protocol

The Neovim layer communicates with the Python CLI via stdin/stdout JSON.

**Input:**
```json
{
  "config": {
    "llm": {
      "enabled": true,
      "endpoint": "...",
      "model": "...",
      "timeout_ms": 30000,
      "temperature": 0.1,
      "eval_mode": "production",
      "api_key": ""
    },
    "context": {
      "dominant_meter": "iambic pentameter",
      "dominant_ratio": 0.85,
      "dominant_line_count": 12
    }
  },
  "lines": [
    { "lnum": 0, "text": "Shall I compare thee to a summer's day?" }
  ]
}
```

**Output:**
```json
{
  "results": [
    {
      "lnum": 0,
      "text": "Shall I compare thee to a summer's day?",
      "meter_name": "iambic pentameter",
      "confidence": 0.9,
      "token_patterns": ["U", "U", "US", "U", "U", "US", "U"],
      "stress_spans": [[9, 12], [24, 28]],
      "meter_overridden": false,
      "override_reason": ""
    }
  ],
  "eval": {
    "mode": "production",
    "result_count": 1,
    "meter_overrides": 0
  }
}
```

## Troubleshooting

- No annotations appear: check `llm.endpoint` and `llm.model`; run `:MeterMeterStatus` for `llm_error`; wait for cooldown if the LLM was repeatedly failing.
- Unexpected highlights: run `:MeterMeterDump` and inspect the JSON at `debug_dump_path`.
- `espeak` warnings from prosodic: install espeak (`brew install espeak` / `apt install espeak`). Without it, OOV words fall back to a neural heuristic which may be slower or less accurate.
