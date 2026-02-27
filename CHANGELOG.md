# Changelog

All notable changes to MeterMeter will be documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- Health check: `:checkhealth metermeter`
- Vimdoc: `:help metermeter`
- Meter hint notes for feminine endings and substitutions (`fem`/`inv`/`spon`/`pyrr`, via `ui.meter_hint_details`)
- `<Plug>` mappings: `(metermeter-toggle)`, `(metermeter-rescan)`, `(metermeter-enable)`, `(metermeter-disable)`
- Commands: `:MeterMeterEnable`, `:MeterMeterDisable`, `:MeterMeterStatus`
- Neovim 0.10+ version guard in `setup()`
- Error surfacing via `vim.notify` on subprocess crash / restart limit
- LuaCATS type annotations on public API
- StyLua formatting and Selene linting in CI
- Luarocks rockspec for rocks.nvim compatibility
- README badges (CI, Neovim version, license)

## [0.1.0] - 2026-02-26

Initial release.

- Real-time poetic meter annotation with stress highlighting and meter labels
- Progressive scanning: visible lines, prefetch, then full buffer
- Per-buffer LRU cache with epoch-based invalidation
- Persistent Python subprocess (prosodic + CMU Dict + eSpeak fallback)
- Automatic statusline integration (lualine or native)
- Comment-aware line filtering via `&comments`/`&commentstring`
- Configurable confidence thresholds, debounce, and trailing-backslash mode
