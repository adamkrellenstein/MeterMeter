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
- `meter_engine.py`: uses prosodic's pronunciation layer (CMU Dict + eSpeak fallback) for lexical stress, with a function-word lookup table for monosyllable disambiguation. Meter is classified by template matching against the resulting stress pattern. Produces a `LineAnalysis` with per-syllable stress positions, per-token patterns, meter name, foot count, and confidence.

### Pipeline

```
line text
  -> prosodic (pronunciation)  -> syllables + lexical stress
  -> monosyllable rules        -> S/U stress pattern
  -> template matching         -> meter_name, confidence
  -> stress span computation   -> byte-level stress spans
  -> Neovim extmarks
```

## Comparison with other English scansion systems

The only widely-used English gold standard is [For Better For Verse](https://github.com/waynegraham/for_better_for_verse) (4B4V): ~1,100 annotated lines across 85 poems, 16th--20th century.

| System | Approach | Per-syllable | Per-line |
|--------|----------|-------------|----------|
| Scandroid (Hartman) | Dict + rules → stress → feet | ~90% | -- |
| ZeuScansion (Agirrezabal 2016) | Dict + POS + Groves rules → stress → template | 86.78% | -- |
| BiLSTM-CRF (Agirrezabal 2017) | Neural sequence labeling | 92.96% | 61.39% |
| **MeterMeter** | **Dict + function-word rules → stress → template** | **~85%** | **~73%** |

Per-syllable accuracy is naturally high because most syllables are unambiguous; per-line is strict because a single wrong stress fails the whole line. The main error source for all systems is context-dependent monosyllable stress (e.g. "hath", "all", "too"), which requires poem-level context to resolve.

### How scansion engines differ

Every system that scores well on per-syllable accuracy uses the same fundamental approach: **stress first, meter second**. Dictionary lookup determines stress for polysyllabic words (~100% correct), a function-word rule handles monosyllables (~80-85% correct), and meter is classified from the resulting pattern. The BiLSTM-CRF learns these mappings jointly from data instead of using hand-crafted rules.

MeterMeter previously used prosodic's OT metrical parser, which works in the opposite direction: it picks the best *metrical grid* for the line and reads stress off the grid positions. This was linguistically principled but fragile -- when the parser picked the wrong template (e.g. trochaic instead of iambic), every syllable flipped, producing ~66% per-syllable accuracy. Switching to lexical stress lookup with monosyllable rules brought per-syllable accuracy to ~85% and made the engine ~80x faster (the OT parse was the bottleneck).

The remaining gap to the BiLSTM-CRF (~93%) is mostly context-dependent monosyllable stress: function words in strong metrical positions (e.g. "to" in "thee TO a summer's day") that our rule marks as unstressed but the gold standard marks as stressed. Closing this gap would require either a trained sequence model or poem-level context awareness.

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

MeterMeter shows the dominant meter in the statusline automatically — no configuration needed. If lualine is installed it injects into `lualine_x`; otherwise it patches the statusline of each metermeter window directly. The meter shows as `MM: iambic pentameter` when active, `MM: …` while the first scan runs, or an error summary on failure.

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

# Full 4B4V corpus (1,181 lines, ~7s)
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
