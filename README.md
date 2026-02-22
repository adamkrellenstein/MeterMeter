# MeterMeter

Local, real-time poetic meter annotation for Neovim.

This repository is Neovim-first. Plugin code lives in `nvim/metermeter.nvim/`.

Scanning is progressive and incremental: visible lines are annotated first, nearby prefetch lines next, then remaining scanable lines across the buffer. Cached results are reused for unchanged lines, and LLM refinements patch in after baseline engine results.

## Architecture

MeterMeter has two layers:

- Neovim runtime layer (`nvim/metermeter.nvim/lua/metermeter/init.lua`)
  - Determines which lines are scanable (ignores native comment lines).
  - Schedules scans with debounce + periodic tick.
  - Runs progressive scan phases (`visible -> prefetch -> rest-of-buffer`).
  - Reuses cached line analyses keyed by `(model, endpoint, line_text)`.
  - Renders stress extmarks and aligned end-of-line meter hints.

- Python analysis layer (`nvim/metermeter.nvim/python/metermeter_cli.py`, `nvim/metermeter.nvim/python/metermeter/meter_engine.py`)
  - Computes deterministic baseline meter + stress spans for requested lines.
  - Uses token-level stress options and template fitting for meter selection.
  - Applies foot-position priors (e.g., initial inversion tolerance) and poetic lexicon overrides.
  - Optionally refines line analysis through an OpenAI-compatible LLM endpoint.

Pipeline behavior is intentionally two-phase:

1. Engine-first annotations appear quickly.
2. LLM refinements patch in afterward (if enabled), line-by-line.

This keeps interaction responsive while still allowing higher final accuracy.

### What The LLM Is Used For

- Refines meter label + confidence when context suggests better line-level scansion.
- Refines per-token stress patterns (used for stress highlighting).
- Optionally provides short poetic notes (`analysis_hint`) when enabled in UI settings.

### What The LLM Is Not Used For

- Not required for baseline scansion: deterministic engine always runs first.
- Not used for editor control flow, gating, or file parsing.
- Not required for offline usage.

## Requirements

- Neovim 0.10+ (uses `vim.system()` and `vim.json`).
- Python 3.8+ (`python3` on PATH).
- Optional (for LLM refinement): Ollama running locally, or any OpenAI-compatible `chat/completions` endpoint.

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

Then MeterMeter will only annotate lines that end with a trailing backslash (`\`).

Comment lines are ignored using the buffer's native `&comments` / `&commentstring`.

## Configuration Reference

All options are passed to:

```lua
require("metermeter").setup({ ... })
```

| Option | Default | What it does | Why it exists |
|---|---:|---|---|
| `debounce_ms` | `80` | Delay after edits before rescanning. | Avoids rescanning on every keystroke burst. |
| `rescan_interval_ms` | `0` | Periodic background rescan interval; `0` disables timer. | Defaults to idle-friendly behavior; enable only if you need periodic rescans. |
| `prefetch_lines` | `5` | Also scans lines around cursor, not only currently visible lines. | Keeps nearby context warm without delaying visible-line feedback. |
| `ui.stress` | `true` | Enables stress span highlighting. | Allows turning off visual emphasis if user only wants meter labels. |
| `ui.meter_hints` | `true` | Shows meter annotations at line end. | Supports a cleaner “highlights only” mode when false. |
| `ui.meter_hint_confidence_levels` | `6` | Number of confidence tint steps for EOL meter text. | Balances subtle confidence signaling vs. visual noise. |
| `ui.show_analysis_hint` | `false` | Enables display of optional LLM poetic note text. | Keeps default UI minimal while allowing richer feedback when requested. |
| `ui.analysis_hint_mode` | `"off"` | Hint display mode: `off` or `inline`. | Lets users choose whether notes should affect layout/visual density. |
| `require_trailing_backslash` | `false` | If true, only scans lines ending with `\`. | Useful for mixed prose/code documents where only explicit poem lines should be scanned. |
| `cache.max_entries` | `5000` | Max number of cached analysis entries per buffer (LRU-style eviction). | Prevents unbounded memory growth in long sessions. |
| `debug_dump_path` | `"/tmp/metermeter_nvim_dump.json"` | Output path used by `:MeterMeterDump`. | Fast, file-based debugging without requiring interactive logging hooks. |
| `llm.enabled` | `true` | Enables LLM refinement layer. | Lets users choose deterministic-only mode when needed. |
| `llm.endpoint` | `http://127.0.0.1:11434/v1/chat/completions` | OpenAI-compatible chat endpoint. | Supports local Ollama and compatible providers uniformly. |
| `llm.model` | `qwen2.5:7b-instruct` | Model name sent to endpoint. | Keeps model choice explicit and user-switchable. |
| `llm.timeout_ms` | `30000` | HTTP timeout for LLM calls. | Prevents scanner stalls on slow/unavailable models. |
| `llm.temperature` | `0.1` | Sampling temperature for refinement requests. | Keeps output mostly deterministic for meter tasks. |
| `llm.max_lines_per_scan` | `2` | Max lines sent to LLM per scan cycle. | Controls latency/cost and keeps editor responsiveness stable. |
| `llm.hide_non_refined` | `false` | Hide meter labels for lines not refined by LLM. | Supports “LLM-only confidence” display preference. |
| `llm.failure_threshold` | `3` | Consecutive LLM errors before cooldown kicks in. | Prevents repeated failing requests from degrading editing responsiveness. |
| `llm.cooldown_ms` | `15000` | Cooldown duration after hitting failure threshold. | Gives local runtime/endpoints time to recover while keeping engine-only mode active. |

Global toggle:

| Variable | Default | What it does | Why it exists |
|---|---:|---|---|
| `vim.g.metermeter_disable_auto_setup` | `0` | If `1`, plugin won’t auto-call `setup()`. | Prevents double-setup and lets plugin managers/users fully control initialization order. |

## Testing Strategy

The repository uses a layered regression strategy:

- Headless Neovim smoke test (`scripts/nvim_smoke_test.lua`)
  - Verifies plugin wiring, extmark rendering, backslash gating, comment filtering, and filetype enable behavior.

- Python unit tests (`tests/test_nvim_*.py`)
  - Stress-span correctness and clipping behavior.
  - LLM parsing/validation/fallback behavior with mocked responses.
- Canonical meter accuracy floors on Shakespeare fixtures:
  - Sonnet 18
  - Sonnet 116
  - Sonnet 130

- Broader corpus regressions:
  - Non-Shakespeare formal sonnet benchmark (Milton) with iambic-pentameter floor.
  - Free-verse behavior guard (Whitman) to avoid over-collapsing to a single meter class.

- Cache/scheduling regressions
  - Duplicate-line correctness: repeated identical lines must annotate on every row.
  - Progressive scheduling: visible lines first, then prefetch, then full-buffer completion.
  - Idle behavior: with unchanged text/viewport, periodic ticks do not trigger extra CLI work or redraw.

Run everything locally:

```bash
./scripts/run_smoke_tests.sh
./scripts/run_static_checks.sh
```

## Performance Model

- Scan order is prioritized: `visible -> prefetch -> rest-of-buffer`.
- Engine pass is always first and incremental.
- LLM pass is incremental and optional.
- Cache is reused across rescans for unchanged lines.
- If LLM repeatedly fails, MeterMeter enters temporary LLM cooldown and continues engine-only annotations.
- Idle guard skips rescans when both buffer text (`changedtick`) and viewport signature are unchanged.

## Troubleshooting

- If meter hints appear but LLM notes/refinements do not:
  - verify `llm.endpoint` and `llm.model`
  - check local runtime health (e.g. Ollama running)
  - wait for cooldown expiry if repeated failures occurred
- Dump runtime state with:
  - `:MeterMeterDump`
  - inspect path in `debug_dump_path` (default `/tmp/metermeter_nvim_dump.json`)
