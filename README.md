# MeterMeter

Local, real-time poetic meter annotation for Neovim.

This repository is Neovim-first. Plugin code lives in `nvim/metermeter.nvim/`.

Scanning is progressive and incremental: visible lines are annotated first, nearby prefetch lines next, then remaining scanable lines across the buffer. Cached LLM results are reused for unchanged lines.

## Architecture

MeterMeter has three layers:

- Neovim runtime layer (`nvim/metermeter.nvim/lua/metermeter/`)
  - Determines which lines are scanable (ignores native comment lines).
  - Schedules scans with debounce + periodic tick.
  - Runs progressive scan phases (`visible -> prefetch -> rest-of-buffer`).
  - Maintains a per-buffer LRU cache keyed by `(model, endpoint, eval_mode, temperature, lexicon_path, extra_lexicon_path, cache_epoch, line_text)`.
  - Renders stress extmarks and aligned end-of-line meter hints.

- Python CLI layer (`nvim/metermeter.nvim/python/metermeter_cli.py`)
  - Bridges Neovim and the analysis engine via stdin/stdout JSON protocol.
  - Applies post-LLM meter overrides (pattern rescore, iambic guard, baseline guard, dominant-meter smoothing) in priority order; first match wins.
  - Computes stress-highlight byte spans from token patterns.

- Python analysis layer (`nvim/metermeter.nvim/python/metermeter/`)
  - `meter_engine.py`: Builds token/syllable scaffolding, scores stress patterns against meter templates, produces baseline analyses.
  - `llm_refiner.py`: Validates strict LLM JSON output (`meter_name`, `confidence`, `token_stress_patterns`) with normalization/repair in production mode.
  - `heuristics.py`: Syllable counting and stress estimation for out-of-vocabulary words.

Pipeline behavior is LLM-first and LLM-required:

1. Meter labels and stress highlights are shown only from valid LLM output.
2. If the model/endpoint is unavailable or response is invalid, no annotations are shown for that scan.
3. Meter labels are post-validated against returned stress patterns; low-confidence outliers can be smoothed toward the dominant poem meter when evidence supports it.

### What The LLM Is Used For

- Refines meter label + confidence when context suggests better line-level scansion.
- Refines per-token stress patterns (used for stress highlighting).
### Deterministic Layer

- Used for tokenization/syllable scaffolding and rendering guardrails.
- Used to rescore LLM labels against stress templates and to apply conservative dominant-meter smoothing.
- Not used as a user-visible meter fallback when LLM is unavailable.

## Requirements

- Neovim 0.10+ (uses `vim.system()` and `vim.json`).
- Python 3.8+ (`python3` on PATH).
- Required: local Ollama (or another OpenAI-compatible `chat/completions` endpoint) with a working model.

## Compatibility Matrix

| Component | Supported |
|---|---|
| Neovim | 0.10+ |
| Python | 3.8+ |
| Platforms tested in CI/local checks | macOS, Linux (headless test path) |

## Install (lazy.nvim)

Use this repo, but point `dir` to the plugin subfolder:

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

Add this to your `init.vim` between `plug#begin(...)` and `plug#end()`:

```vim
Plug '~/dev/MeterMeter/nvim/metermeter.nvim'
```

Then restart Neovim and run:

```vim
:PlugInstall
```

Optional (recommended for nicer highlight colors):

```vim
set termguicolors
```

## Usage

- `:MeterMeterToggle`
- `:MeterMeterRescan`
- `:MeterMeterDump` (writes `/tmp/metermeter_nvim_dump.json`)
- `:MeterMeterStatus` (shows effective enable state, auto/filetype match, override, LLM cooldown)

### Statusline

`require("metermeter").statusline()` returns a short string for statusline integration:

- `""` when MeterMeter is inactive on the current buffer
- `"MM: iambic pentameter"` (or whatever the dominant meter is)
- `"MM: scanning"` while a scan is in progress
- `"MM: <error>"` when the LLM is failing (truncated to 60 chars)

Example for a custom statusline:

```vim
set statusline+=%{luaeval("require('metermeter').statusline()")}
```

Or with lualine:

```lua
require("lualine").setup({
  sections = {
    lualine_x = { function() return require("metermeter").statusline() end },
  },
})
```

## File Enable Rules

MeterMeter enables itself when `&filetype` includes `metermeter`.

- `*.poem` is detected automatically via `ftdetect` (filetype becomes `metermeter`).
- Mixed-format files can opt-in via a modeline, for example:

```text
vim: set ft=typst.metermeter :
```

By default, MeterMeter annotates every non-comment line.

For ad hoc non-`.poem` files (e.g. `typst`, `markdown`), just use:

```vim
:MeterMeterToggle
```

`MeterMeterToggle` applies a buffer-local override:
- toggle on: force-enable for this buffer
- toggle off: disable for this buffer

If you want an explicit "poetry line marker" for mixed-format files, set:

```lua
require("metermeter").setup({ require_trailing_backslash = true })
```

Or use a regular variable (no setup change required):

```vim
" global
let g:metermeter_require_trailing_backslash = 1

" current buffer only
let b:metermeter_require_trailing_backslash = 1
```

Then MeterMeter will only annotate lines that end with a trailing backslash (`\`).

Comment lines are ignored using the buffer's native `&comments` / `&commentstring`.

### Large Vocabulary Default

If a large lexicon file exists at `~/.metermeter/cmudict.json.gz` (or `METERMETER_LEXICON_PATH` is set),
MeterMeter will use it automatically without extra config. You can still override with `lexicon_path`.

## Configuration Reference

All options are passed to:

```lua
require("metermeter").setup({ ... })
```

### Core Options

| Option | Default | What it does | Why it exists |
|---|---:|---|---|
| `debounce_ms` | `80` | Delay after edits before rescanning. | Avoids rescanning on every keystroke burst. |
| `rescan_interval_ms` | `0` | Periodic background rescan interval; `0` disables timer. | Defaults to idle-friendly behavior; enable only if you need periodic rescans. |
| `prefetch_lines` | `5` | Also scans lines around cursor, not only currently visible lines. | Keeps nearby context warm without delaying visible-line feedback. |
| `ui.stress` | `true` | Enables stress span highlighting. | Allows turning off visual emphasis if user only wants meter labels. |
| `ui.meter_hints` | `true` | Shows meter annotations at line end. | Supports a cleaner “highlights only” mode when false. |
| `ui.meter_hint_confidence_levels` | `6` | Number of confidence tint steps for EOL meter text. | Balances subtle confidence signaling vs. visual noise. |
| `ui.show_error_hint` | `true` | Shows a small inline error hint when LLM fails. | Makes LLM failures visible without checking `:MeterMeterStatus`. |
| `require_trailing_backslash` | `false` | If true, only scans lines ending with `\`. | Useful for mixed prose/code documents where only explicit poem lines should be scanned. |
| `llm.enabled` | `true` | Enables meter analysis pipeline. Must stay `true` for annotations. | Meter output is LLM-required. |
| `llm.endpoint` | `http://127.0.0.1:11434/v1/chat/completions` | OpenAI-compatible chat endpoint. | Supports local Ollama and compatible providers uniformly. |
| `llm.model` | `qwen2.5:7b-instruct` | Model name sent to endpoint. | Keeps model choice explicit and user-switchable. |
| `llm.timeout_ms` | `30000` | HTTP timeout for LLM calls. | Prevents scanner stalls on slow/unavailable models. |
| `llm.temperature` | `0.1` | Sampling temperature for refinement requests. | Keeps output mostly deterministic for meter tasks. |
| `llm.eval_mode` | `"production"` | LLM evaluation mode: `production` or `strict`. | `strict` disables normalization/repair so tests can measure raw model quality. |
| `llm.max_concurrent` | `1` | Maximum concurrent LLM requests per scan phase. | Speeds up larger buffers when GPU is available. |

### Advanced Options

| Option | Default | What it does | Why it exists |
|---|---:|---|---|
| `cache.max_entries` | `5000` | Max number of cached analysis entries per buffer (LRU-style eviction). | Prevents unbounded memory growth in long sessions. |
| `debug_dump_path` | `"/tmp/metermeter_nvim_dump.json"` | Output path used by `:MeterMeterDump`. | Fast, file-based debugging without requiring interactive logging hooks. |
| `llm.max_lines_per_scan` | `2` | Max lines sent to LLM per scan chunk (each scan phase chunks lines by this value). | Controls latency/cost and keeps editor responsiveness stable. |
| `llm.failure_threshold` | `3` | Consecutive LLM errors before cooldown kicks in. | Prevents repeated failing requests from thrashing the editor. |
| `llm.cooldown_ms` | `15000` | Cooldown duration after hitting failure threshold. | Gives local runtime/endpoints time to recover before retry. |
| `lexicon_path` | `""` | Optional path to a large word-pattern JSON (or `.json.gz`) lexicon. When empty, auto-resolved from `~/.metermeter/cmudict.json.gz` or `METERMETER_LEXICON_PATH`. | Allows full CMUdict-sized vocab without bundling it. |
| `extra_lexicon_path` | `""` | Optional extra lexicon path to merge on top of base patterns. | Lets you add custom vocabulary without replacing the main lexicon. |

Global toggle:

| Variable | Default | What it does | Why it exists |
|---|---:|---|---|
| `vim.g.metermeter_disable_auto_setup` | `0` | If `1`, plugin won’t auto-call `setup()`. | Prevents double-setup and lets plugin managers/users fully control initialization order. |

## Testing Strategy

The repository uses a layered regression strategy:

- Headless Neovim smoke test (`scripts/nvim_smoke_test.lua`)
  - Verifies plugin wiring, extmark rendering, backslash gating, comment filtering, and filetype enable behavior.

- Python unit tests (`tests/test_nvim_*.py`)
  - Stress-span correctness, clipping behavior, and multi-byte UTF-8 byte-index mapping.
  - LLM parsing/validation behavior with mocked responses.
  - Deterministic stress-pattern rescoring tests (meter-template consistency).
  - Non-iambic template regression tests (trochaic/anapestic/dactylic stress patterns).
  - Canonical meter accuracy floors on Shakespeare fixtures:
    - Sonnet 18
    - Sonnet 116
    - Sonnet 130

- Broader corpus regressions:
  - Non-Shakespeare formal sonnet benchmark (Milton) with iambic-pentameter floor.
  - Free-verse behavior guard (Whitman) to avoid over-collapsing to a single meter class.
  - Targeted known-regression lines (Shakespeare + Milton) to protect specific historical failure cases.
    - Currently skipped due to `pattern_best_meter` instability across local models.

- Cache/scheduling regressions:
  - Duplicate-line correctness: repeated identical lines must annotate on every row.
  - Progressive scheduling: visible lines first, then prefetch, then full-buffer completion.
  - Idle behavior: with unchanged text/viewport, periodic ticks do not trigger extra CLI work or redraw.

Run everything locally:

```bash
./scripts/run_all_checks.sh
./scripts/run_smoke_tests.sh
./scripts/run_static_checks.sh
```

Run real LLM integration accuracy checks (opt-in):

```bash
export METERMETER_LLM_INTEGRATION=1
export METERMETER_LLM_PROGRESS=1
export METERMETER_LLM_ENDPOINT="http://127.0.0.1:11434/v1/chat/completions"
export METERMETER_LLM_MODEL="qwen2.5:7b-instruct"
./scripts/run_llm_integration_tests.sh
```

For extended corpora (more sonnets + non-iambic meters + iambic tetrameter), enable:

```bash
export METERMETER_LLM_EXTENDED=1
```

`tests/test_nvim_llm_integration.py` is intentionally skipped unless `METERMETER_LLM_INTEGRATION=1`.
It validates real-model output and enforces integration floors on:
- Sonnet 18 (iambic pentameter)
- Sonnet 116 (iambic pentameter)
- Sonnet 130 (iambic pentameter)
- Milton, *On His Blindness* (iambic pentameter)
- Whitman opening excerpt (free-verse anti-collapse guard)
- Scansion quality floor on strict iambic lines (token-level stress pattern match)
Extended suite adds:
- Sonnet 29 + Sonnet 73 (iambic pentameter)
- Trochaic tetrameter (Longfellow)
- Anapestic tetrameter ("Night Before Christmas")
- Dactylic hexameter (Longfellow, *Evangeline*)
- Iambic tetrameter (Frost, *Stopping by Woods* opening stanza)

For raw-model benchmarking (no normalization/repair), set:

```bash
export METERMETER_LLM_EVAL_MODE=strict
```

## Performance Model

- Scan order is prioritized: `visible -> prefetch -> rest-of-buffer`.
- LLM pass is incremental and cached.
- Cache is reused across rescans for unchanged lines.
- If LLM repeatedly fails, MeterMeter enters temporary cooldown and suppresses annotations until healthy again.
- Idle guard skips rescans when both buffer text (`changedtick`) and viewport signature are unchanged.

## Troubleshooting

- If annotations do not appear:
  - verify `llm.endpoint` and `llm.model`
  - check local runtime health (e.g. Ollama running and model available)
  - run `:MeterMeterStatus` to inspect `llm_error`
  - wait for cooldown expiry if repeated failures occurred
- Dump runtime state with:
  - `:MeterMeterDump`
  - inspect path in `debug_dump_path` (default `/tmp/metermeter_nvim_dump.json`)
