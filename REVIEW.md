# Codebase Quality Review

Comprehensive review of MeterMeter across quality, robustness, testing, documentation, elegance, and simplicity.

**Summary**: This is a well-crafted project with a clear architectural vision, strong test coverage, excellent documentation, and thoughtful domain-specific design. The issues identified below are primarily opportunities for refinement rather than fundamental problems.

---

## 1. Quality

### Strengths

- **Clean three-layer architecture.** Neovim UI, IPC subprocess, and Python analysis engine have well-defined boundaries and responsibilities. No layer bleeds into another.
- **Consistent code style** across both Python and Lua, with clear naming conventions throughout.
- **Well-structured data flow.** The pipeline from text → pronunciation → stress → meter → highlights is linear and easy to follow.
- **Good use of dataclasses.** `LineAnalysis`, `_SyllableUnit`, `_MeterPath` are clean data containers with appropriate defaults.
- **Type annotations.** Python code has proper type hints on all public interfaces and most internals.
- **Constants are well-named and grouped.** Scoring constants are at module-level with descriptive names and documented provenance (e.g., monosyllable word lists annotated with 4B4V corpus percentages).

### Issues

1. **LRU cache eviction is O(n).** `_cache_put` in `init.lua:285-313` iterates all entries to find the oldest on every insert at capacity. With `max_entries=5000`, this could add latency. A doubly-linked list or an age-ordered structure would make eviction O(1).

2. **`analyze_line` is a 130-line monolith** (`meter_engine.py:560-689`). It handles tokenization, prosodic lookup, syllable alignment, Viterbi disambiguation, flip-cost filtering, and output assembly all in one method. Decomposing into ~5 named steps would improve readability and testability.

3. **Duplicated iambic bias logic.** The iambic bias check appears in both `best_meter_for_stress_pattern` (lines 278-292) and `_best_meter_for_ambiguous_syllables` (lines 476-488) with nearly identical code. A shared helper would reduce maintenance risk.

4. **Duplicated meter candidate enumeration.** `_meter_candidates` (line 238) and `_candidate_meters_for_syllables` (line 357) implement the same foot/feet enumeration logic independently.

5. **`_parse_meter_name` uses linear search.** Lines 155-157 iterate `LINE_NAME_BY_FEET` to find a match. A reverse dict (`FEET_BY_LINE_NAME`) would be O(1).

6. **Mutable request in `subprocess.lua`.** `M.send` mutates the `request` table by setting `request.id = id` (line 133). This side-effect could surprise callers if they reuse the table.

---

## 2. Robustness

### Strengths

- **Restart limiting.** `subprocess.lua` has a well-designed restart limiter (3 restarts per 60-second window) that prevents infinite restart loops.
- **Graceful degradation.** Failed subprocess spawns, JSON parse errors, and timeouts are all handled without crashing the editor.
- **Buffer validity checks.** Nearly every Lua function checks `nvim_buf_is_valid` before operating on a buffer.
- **Scan generation tracking.** Stale async results are discarded via `scan_generation` comparison, preventing out-of-order results from corrupting state.
- **Defensive context coercion.** `_coerce_context` validates and clamps all context parameters, including type checks and meter name parsing.
- **UTF-8 byte index mapping.** Careful char-to-byte conversion handles multi-byte characters correctly, with dedicated test coverage.
- **Timeout protection.** `_analyze_with_timeout` in tests prevents prosodic hangs from blocking CI.

### Issues

1. **No error handling for `uv.new_timer()`.** In `init.lua` (lines 389, 802, 820), `uv.new_timer()` could theoretically return nil, which would crash on the subsequent `:start()` call.

2. **Unbounded `stdout_buf` growth.** In `subprocess.lua:47`, if the Python subprocess emits a malformed response without a newline, `stdout_buf` grows without bound. A maximum buffer size or periodic truncation would be defensive.

3. **Silent JSON decode failures in `run_persistent`.** `metermeter_cli.py:94-95` silently `continue`s on malformed JSON with no error feedback. A malformed request is indistinguishable from no request. Consider logging to stderr or returning an error response.

4. **`SIGALRM` timeout is Unix-only.** `test_nvim_stress_accuracy.py` uses `signal.SIGALRM` which doesn't exist on Windows. Since CI runs on Ubuntu/macOS only, this is acceptable, but limits portability.

5. **No `stdin_pipe` nil-check in `send`.** `subprocess.lua:137` calls `uv.write(stdin_pipe, ...)` but `stdin_pipe` could be nil if the process was killed between `ensure_running` and `send`.

---

## 3. Testing

### Strengths

- **Multi-layered test strategy.** Unit tests, protocol tests, accuracy regression tests, integration tests, and headless Neovim smoke tests cover different failure modes at different levels.
- **Accuracy floors with regression guards.** Tests enforce minimum thresholds (>=85% syllable, >=73% meter classification, >=83% iambic pentameter F1) that prevent regressions.
- **Corpus diversity.** Milton, Whitman, three Shakespeare sonnets, plus the 4B4V corpus provide broad coverage.
- **Free-verse guardrail.** The Whitman test ensures the model doesn't collapse free verse into a single meter class — a clever anti-overfitting check.
- **Byte-span correctness.** Thorough UTF-8 boundary testing including em-dashes, accented characters, and apostrophe tokens.
- **Protocol coverage.** Shutdown, empty lines, context passing, multi-request sequencing, and required response fields are all tested.
- **Headless Neovim integration.** 8 scenarios (backslash gate, comment ignore, filetype token, duplicate lines, manual toggle, idle stability, confidence shading, loading indicator) test real editor behavior end-to-end.

### Issues

1. **No test for subprocess crash recovery.** The restart-limiting logic in `subprocess.lua` (lines 17-29) is untested. A test that kills the subprocess and verifies restart behavior would validate the most critical robustness mechanism.

2. **No test for LRU cache eviction.** The `_cache_put` eviction path (when `cache_size > max_entries`) is never exercised in tests.

3. **No test for `_align_syllables_in_token` fallback.** The proportional-width fallback path (`meter_engine.py:544-558`) when exact substring matching fails is untested.

4. **`conftest.py` uses `sys.path` manipulation instead of proper packaging.** This works but is fragile — if file paths change, tests silently fail to import. Installing the package in editable mode (`pip install -e .`) would be more robust.

5. **Some tests create a new `MeterEngine()` per test method** (e.g., `test_nvim_meter_rescoring.py`) instead of using `setUp`. This is a minor inefficiency given the lightweight constructor, but inconsistent with `test_engine_edge_cases.py` which does use `setUp`.

---

## 4. Documentation

### Strengths

- **Excellent README.** Architecture section, pipeline diagram, comparison table with published systems, configuration reference, troubleshooting section, JSON protocol specification, and install instructions for two plugin managers.
- **Scholarly context.** Comparison with Scandroid, ZeuScansion, and BiLSTM-CRF gives users and contributors a clear frame of reference.
- **Design rationale.** "Why monosyllables are hard" and "Design tradeoffs" sections explain non-obvious decisions.
- **Metrics transparency.** Clear about what metrics mean and how they differ from published numbers in other systems.
- **In-code annotations.** Monosyllable word lists include 4B4V corpus percentages (e.g., `"all", # 96% S`).

### Issues

1. **No docstrings in `meter_engine.py`.** The core 689-line engine has zero docstrings on public methods. `analyze_line`, `best_meter_for_stress_pattern`, and `score_stress_pattern_for_meter` are the public API and deserve documentation of parameters, return values, and behavior.

2. **No module-level doc comments in Lua modules.** `init.lua`, `scanner.lua`, `state.lua`, and `subprocess.lua` have no leading documentation explaining their role.

3. **`_SyllableUnit` and `_MeterPath` lack field documentation.** These internal dataclasses have no docstrings or field descriptions. Given their importance to the Viterbi algorithm, brief docs would help new contributors.

---

## 5. Elegance & Simplicity

### Strengths

- **Single persistent subprocess.** Elegant solution to startup latency — spawn once, reuse for the session. No worker pool complexity.
- **Progressive scan phases.** Visible → prefetch → background is a natural priority ordering that delivers perceived responsiveness with minimal complexity.
- **Text-keyed cache.** `(epoch, text)` as cache key is simple but effective — identical text is reused, and bumping the epoch invalidates everything.
- **Soft priors architecture.** Function-word lists as probabilistic priors (not hard rules) with Viterbi resolution is the right abstraction for this domain.
- **Two-tier confidence display.** A simple bright/dim UX rather than a complex gradient — appropriate for an annotation tool.
- **Newline-delimited JSON protocol.** Dead simple, human-readable, debuggable, no framing complexity.
- **`ftdetect` + filetype token.** Minimal, composable activation mechanism that integrates naturally with Neovim's filetype system.

### Issues

1. **Dual scoring paths.** Pattern scoring is implemented twice — once for the deterministic public API (`_score_pattern_for_meter` + `best_meter_for_stress_pattern`) and once for the Viterbi path (`_best_meter_for_ambiguous_syllables`). Both have their own iambic bias logic, meter enumeration, and scoring. This is the largest source of internal duplication and the highest maintenance risk.

2. **`init.lua` at 1,079 lines is large.** It handles UI rendering, LRU caching, scan orchestration, subprocess lifecycle, statusline injection, comment detection, and highlight management. The caching and comment-detection logic could be separate modules.

3. **Duplicated alignment-column logic.** `_refresh_loading` (init.lua:349-377) and `apply_results` (init.lua:441-465) both compute `max_w` + `eol_col` with the same window-clamping pattern. A shared helper would DRY this up.

4. **`metermeter_cli.py` duplicates request validation.** Both `main()` (lines 133-138) and `run_persistent()` (lines 104-109) have identical list comprehensions for filtering valid line items. A shared `_parse_lines(req)` helper would eliminate this.

---

## Prioritized Recommendations

| Priority | Issue | Effort |
|----------|-------|--------|
| **High** | Add docstrings to `meter_engine.py` public methods | Low |
| **High** | Extract `_apply_iambic_bias()` to deduplicate bias logic | Low |
| **High** | Guard `stdin_pipe` nil in `subprocess.lua:send` | Trivial |
| **Medium** | Decompose `analyze_line` into named steps | Medium |
| **Medium** | Extract shared `_eol_column()` helper in `init.lua` | Low |
| **Medium** | Add subprocess restart/recovery test | Medium |
| **Medium** | Extract shared `_parse_lines()` in `metermeter_cli.py` | Low |
| **Low** | Improve LRU cache eviction to O(1) | Medium |
| **Low** | Add LRU cache eviction test | Low |
| **Low** | Add `_align_syllables_in_token` fallback test | Low |
