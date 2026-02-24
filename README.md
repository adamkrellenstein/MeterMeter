# MeterMeter

Local, real-time poetic meter annotation for Neovim.

This repository is Neovim-first. Plugin code lives in `nvim/metermeter.nvim/`.

Scanning is progressive and incremental: visible lines are annotated first, nearby prefetch lines next, then remaining scannable lines across the buffer. Cached results are reused for unchanged lines.

## Architecture

Two layers:

**Neovim runtime** (`nvim/metermeter.nvim/lua/metermeter/`)
- Determines which lines are scannable (ignores native comment lines).
- Schedules scans with debounce and periodic tick.
- Runs progressive scan phases: `visible -> prefetch -> rest-of-buffer`.
- Maintains a per-buffer LRU cache keyed by `(cache_epoch, line_text)`.
- Renders stress extmarks and aligned end-of-line meter hints.
- Manages a single persistent Python subprocess via `subprocess.lua` (spawned lazily, kept alive for the session).

**Python subprocess** (`nvim/metermeter.nvim/python/metermeter_cli.py`)
- Communicates with Neovim via newline-delimited JSON over stdin/stdout.
- Uses a `ProcessPoolExecutor` (sized to half the CPU count by default) to analyze lines in parallel.
- Each pool worker holds a pre-initialized `MeterEngine`, avoiding repeated prosodic import overhead.

**Python analysis** (`nvim/metermeter.nvim/python/metermeter/`)
- `meter_engine.py`: wraps the `prosodic` OT metrical parser. Produces a `LineAnalysis` with per-syllable stress positions, per-token patterns, meter name, foot count, and confidence.

### Pipeline

```
line text
  -> MeterEngine (prosodic)  -> meter_name, confidence, syllable_positions
  -> stress span computation -> byte-level stress spans
  -> Neovim extmarks
```

### Design rationale

Per-line meter accuracy is inherently limited for all systems because context-dependent monosyllable stress (e.g. "hath", "all", "too") can only be resolved with poem-level context.

### Benchmark results

On the For Better For Verse (4B4V) annotated corpus (1,181 lines, 85 poems):

| System | Per-syllable | Per-line meter |
|--------|-------------|---------------|
| ZeuScansion (FST + rules, 2016) | 86.78% | -- |
| BiLSTM-CRF (Agirrezabal, 2017) | 92.96% | 61.39% |
| MeterMeter (prosodic) | ~66% | ~71% |

Note: per-syllable and per-line measure different things. Per-syllable is high because most syllables are unambiguous; per-line is low because a single monosyllable stress error fails the whole line. MeterMeter optimizes for per-line accuracy (the user-facing metric).

## Requirements

- Neovim 0.10+
- Python 3.11+ (the plugin prefers `.venv/bin/python3` from the project root, falling back to `python3` on PATH)
- [`prosodic`](https://github.com/quadrismegistus/prosodic) Python package (installed in the venv)
- `espeak` system package (used by prosodic for out-of-vocabulary words; install with `brew install espeak` or `apt install espeak`)

## Install (lazy.nvim)

```lua
{
  dir = "~/dev/MeterMeter/nvim/metermeter.nvim",
  config = function()
    require("metermeter").setup()
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
- `:MeterMeterStatus` (shows enable state, filetype match)

### Statusline

`require("metermeter").statusline()` returns:

- `""` when inactive
- `"MM: iambic pentameter"` (or dominant meter)
- `"MM: error: ModuleNotFoundError: ..."` on CLI failure
- `"MM: scanning"` during a scan
- `"MM"`

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

| Option | Default | Description |
|--------|---------|-------------|
| `debounce_ms` | `80` | Delay after edits before rescanning. |
| `rescan_interval_ms` | `0` | Periodic rescan interval; `0` disables. |
| `prefetch_lines` | `5` | Lines scanned around cursor beyond visible range. |
| `require_trailing_backslash` | `false` | Only annotate lines ending with `\`. |
| `ui.stress` | `true` | Enable stress span highlighting. |
| `ui.meter_hints` | `true` | Show meter label at end of line. |
| `ui.meter_hint_confidence_levels` | `6` | Number of confidence tint steps (range 2-12). |
| `cache.max_entries` | `5000` | Per-buffer LRU cache capacity. |
| `debug_dump_path` | `"/tmp/metermeter_nvim_dump.json"` | Output path for `:MeterMeterDump`. |

### Global variable

| Variable | Default | Description |
|----------|---------|-------------|
| `vim.g.metermeter_disable_auto_setup` | `0` | Set to `1` to suppress automatic `setup()` call. |

## Testing

```bash
uv run pytest tests/ -q
```

**Corpus accuracy test** (runs 14 lines by default; set `MM_TEST_MAX_LINES=0` for the full corpus):

```bash
uv run pytest tests/test_nvim_stress_accuracy.py

# Full 4B4V corpus (1,181 lines, ~30s)
MM_TEST_MAX_LINES=0 uv run pytest tests/test_nvim_stress_accuracy.py -v -s
```

**Benchmark** (requires 4B4V corpus in `benchmarks/data/poems/`):

```bash
uv run python benchmarks/run_benchmark.py --progress
```

**Neovim smoke test** (requires `nvim` on PATH):

```bash
./scripts/run_smoke_tests.sh
```

## Subprocess JSON Protocol

The Neovim layer communicates with the Python subprocess via newline-delimited JSON over stdin/stdout. Each message is a single line.

**Request (Lua → Python):**
```json
{"id": 1, "lines": [{"lnum": 0, "text": "Shall I compare thee to a summer's day?"}]}
```

**Response (Python → Lua):**
```json
{"id": 1, "results": [{"lnum": 0, "text": "Shall I compare thee to a summer's day?", "meter_name": "iambic pentameter", "confidence": 0.9, "stress_spans": [[9, 12], [24, 28]]}], "eval": {"line_count": 1, "result_count": 1}}
```

The `id` is echoed in the response so Lua can match responses to callbacks (requests and responses may interleave). Shutdown: `{"shutdown": true}` causes the subprocess to exit cleanly.

## Troubleshooting

- No annotations appear: check that `&filetype` includes `metermeter`; run `:MeterMeterStatus`.
- Unexpected highlights: run `:MeterMeterDump` and inspect the JSON at `debug_dump_path`.
- `espeak` warnings from prosodic: install espeak (`brew install espeak` / `apt install espeak`). Without it, OOV words fall back to a neural heuristic which may be slower or less accurate.
