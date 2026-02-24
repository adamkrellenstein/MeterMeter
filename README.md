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

## Comparison with other English scansion systems

The only widely-used English gold standard is [For Better For Verse](https://github.com/waynegraham/for_better_for_verse) (4B4V): ~1,100 annotated lines across 85 poems, 16th--20th century.

| System | Approach | Per-syllable | Per-line |
|--------|----------|-------------|----------|
| ZeuScansion (Agirrezabal 2016) | FST + rules | 86.78% | -- |
| BiLSTM-CRF (Agirrezabal 2017) | Neural sequence labeling | 92.96% | 61.39% |
| MeterMeter | OT constraints (prosodic) | ~66% | ~71% |

Per-syllable accuracy is naturally high because most syllables are unambiguous; per-line is strict because a single wrong stress fails the whole line. MeterMeter optimizes for per-line (the user-facing metric). The main error source for all systems is context-dependent monosyllable stress (e.g. "hath", "all", "too"), which requires poem-level context to resolve.

### Architectural landscape

English scansion systems fall into three families:

**Rule-based / deterministic** -- ZeuScansion (FST), libEscansión (Spanish, 97% line accuracy via recursive precedence). High precision on unambiguous lines, but brittle on edge cases. libEscansión's key insight -- try the most natural phonological adjustments first and only escalate when rules are exhausted -- transfers well to English.

**Neural** -- Agirrezabal's BiLSTM-CRF, Klesnilová et al.'s syllable-embedding BiLSTM-CRF (Czech, 98.9% line accuracy). Strong on ambiguity resolution but require large training corpora that don't exist for English meter. LLMs specifically fail at precise structural tasks when used alone (MetricalARGS, Chalamalasetti et al. 2025).

**OT constraint-based** -- MeterMeter (via prosodic). Evaluates candidate parses against Hanson & Kiparsky's Optimality Theory constraints (\*W/PEAK, \*S/UNSTRESS, FOOTMIN), producing weighted scores rather than binary judgments. Architecturally the most linguistically grounded approach, but prosodic's exhaustive parse generation is expensive and the CMU Pronouncing Dictionary creates systematic errors on pre-modern verse (wrong stress patterns, missing syllabic "-ed", unencoded elisions).

MeterMeter's current accuracy is limited primarily by pronunciation coverage (CMU Dict is modern American English only) and the lack of poem-level context in per-line analysis. The highest-leverage improvements are an Early Modern English pronunciation override layer and constrained candidate generation to replace prosodic's exponential parse space.

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
- `:MeterMeterRescan` — invalidates cache and re-analyzes the whole buffer from scratch
- `:MeterMeterDebug` — prints a one-line summary (works even when disabled) and writes full state JSON to `debug_dump_path`

### Statusline

If lualine is installed, MeterMeter automatically injects itself into `lualine_x` on startup — no configuration needed. The component returns:

- `""` when inactive (hidden by lualine)
- `"MM: iambic pentameter"` (dominant meter, always shown when active)
- `"MM: …"` while the first scan is in progress
- `"MM: error: ModuleNotFoundError: ..."` on subprocess failure

For other statusline plugins, call `require("metermeter").statusline()` from your config.

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

- No annotations appear: check that `&filetype` includes `metermeter` and that the statusline shows `MM: …` or a meter name.
- Unexpected highlights: run `:MeterMeterDump` and inspect the JSON at `debug_dump_path`.
- `espeak` warnings from prosodic: install espeak (`brew install espeak` / `apt install espeak`). Without it, OOV words fall back to a neural heuristic which may be slower or less accurate.
