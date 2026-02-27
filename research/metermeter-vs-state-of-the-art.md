# MeterMeter vs. the State of the Art in Computational Scansion

MeterMeter is a Neovim plugin for real-time poetic meter annotation. It is **deterministic**: it does not require an LLM, and it produces the same output for the same input (given the same pronunciation resources).

For current implementation details and benchmarks, see `README.md`.

## What MeterMeter Does

- **Tokenizes** a line into word tokens (Unicode-aware, with basic apostrophe handling).
- Uses **prosodic** for pronunciation (CMU Dict + eSpeak fallback) to get syllables and lexical stress.
- Applies **soft priors** for common monosyllabic function words (which often behave as clitics in verse).
- Runs a **meter-aware Viterbi** decode over per-syllable `U`/`S` options to choose a globally consistent stress pattern.
- Scores candidate meters against the resolved pattern to output a meter label (e.g. `iambic pentameter`) and a confidence.
- Produces **per-syllable character spans** so Neovim can highlight stress in the original text.

## Architecture (Repo Reality)

MeterMeter is split into three runtime layers:

1. **Neovim runtime** (`nvim/metermeter.nvim/lua/metermeter/`)
   - Debounce + progressive scan scheduling (visible → prefetch → rest).
   - Per-buffer LRU cache keyed by line text.
   - Rendering via extmarks (stress spans + end-of-line meter hints).
   - A single persistent Python subprocess for low latency.

2. **Python subprocess** (`nvim/metermeter.nvim/python/metermeter_cli.py`)
   - Newline-delimited JSON over stdin/stdout.
   - One process for the session; no worker pool.
   - Accepts optional poem-level context (`dominant_meter`, `dominant_strength`) as a soft prior.

3. **Python analysis** (`nvim/metermeter.nvim/python/metermeter/meter_engine.py`)
   - Pronunciation + syllable extraction via prosodic.
   - Function-word priors + ambiguity costs.
   - Viterbi decoding + meter template scoring.
   - Robust character span alignment for highlighting.

## How This Compares to Research Systems

The landscape for English scansion is usually “stress first, meter second”:

- **Rule-based + lexicon** (e.g. Scandroid, ZeuScansion): look up stress (CMU dict), then apply heuristics/constraints and classify meter from the resulting pattern.
- **Prosodic/OT-style parsing**: pick a best metrical grid/parse and infer stress/prominence, typically exploring many parses.
- **Learned sequence models** (e.g. BiLSTM-CRF and later transformer approaches): learn stress assignment directly from annotated corpora.

MeterMeter sits closest to the first family (lexicon + rules), with two editor-driven priorities:

1. **Low latency and stability** (for real-time feedback while writing).
2. **Transparent, debuggable decisions** (costs/scores you can inspect rather than opaque model weights).

## Strengths

- **Editor-native UX**: real-time feedback in the same buffer you’re writing.
- **Deterministic output**: suitable for regression tests and benchmarking.
- **Fast enough for interactive use**: persistent subprocess + incremental scheduling.
- **Robust highlighting**: maps syllables back to character spans for accurate extmarks.

## Limitations (Compared to SOTA)

- **English-only, stress-based meter**: not a quantitative meter engine (Latin/Greek).
- **No syntactic / discourse model**: context-sensitive monosyllable stress remains the hard case; research systems often use POS/parse features or learned context.
- **No global poem-level parse**: context is a soft prior, not a joint optimization across the whole poem.

## Summary Table (High Level)

| System family | Typical approach | Strength | Common weakness |
|---|---|---|---|
| Dict + rules | Lexical stress + heuristics → meter | Interpretable, efficient | Context-sensitive function words |
| OT / constraint parsing | Search parses under constraints | Linguistically principled | Can be slow/fragile in practice |
| Learned sequence models | Train on gold annotations | Strong context modeling | Harder to interpret; needs data |
| **MeterMeter** | Dict + priors + Viterbi in-editor | Real-time + deterministic | Not a learned contextual model |

