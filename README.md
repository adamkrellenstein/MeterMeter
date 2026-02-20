# Poetry Meter (Sublime Text 4)

Real-time poetic meter annotation for Sublime Text 4.

## Features

- Debounced live line-by-line scansion while you type.
- Inline phantom annotations with:
  - stress pattern glyphs (`˘` and `/`)
  - best-guess named meter (for major feet)
  - confidence percentage
- Optional local LLM refinement that can override meter classification and add per-line craft hints.
- Offline stress lookup using bundled compressed dictionary with heuristic fallback.
- Commands to toggle live mode, rescan, and clear annotations.

## Install (Local Package)

1. Open Sublime Text 4.
2. Go to `Preferences -> Browse Packages...`.
3. Create a folder named `PoetryMeter` and copy package files into it.
4. Restart Sublime Text or run `Tools -> Developer -> Reload Plugins`.
5. Ensure `Packages/PoetryMeter/.python-version` exists with `3.8` so ST4 uses modern plugin host.

## Commands

Use Command Palette:

- `Poetry Meter: Toggle Live Annotation`
- `Poetry Meter: Rescan Buffer`
- `Poetry Meter: Clear Annotations`
- `Poetry Meter: Clear LLM Cache`
- `Poetry Meter: Setup Local LLM`
- `Poetry Meter: Bootstrap Local LLM`
- `Poetry Meter: Restart LLM Sidecar`
- `Poetry Meter: Stop LLM Sidecar`

## Settings

`Preferences -> Package Settings -> PoetryMeter -> Settings`

```json
{
    "enabled_by_default": true,
    "debounce_ms": 50,
    "max_line_length": 220,
    "max_buffer_lines_for_live": 10000,
    "display_confidence": true,
    "confidence_threshold": 0.0,
    "annotation_position": "eol",
    "scan_visible_only_when_large": true,
    "dictionary_path_override": null,
    "llm_enabled": true,
    "llm_endpoint": "http://127.0.0.1:11434/v1/chat/completions",
    "llm_model": "llama3.1:8b-instruct",
    "llm_api_key": "",
    "llm_timeout_ms": 1000,
    "llm_temperature": 0.1,
    "llm_max_lines_per_scan": 2,
    "llm_override_meter": true,
    "llm_show_hint": true,
    "llm_prompt_version": "v1",
    "llm_error_cooldown_ms": 3000,
    "llm_sidecar_auto_start": false,
    "llm_sidecar_binary_path": "",
    "llm_sidecar_model_path": "",
    "llm_sidecar_host": "127.0.0.1",
    "llm_sidecar_port": 0,
    "llm_sidecar_command_template": [
        "{binary_path}",
        "--model",
        "{model_path}",
        "--host",
        "{host}",
        "--port",
        "{port}"
    ],
    "llm_sidecar_startup_timeout_ms": 12000,
    "llm_sidecar_stop_timeout_ms": 2000,
    "llm_sidecar_cooldown_ms": 5000,
    "llm_sidecar_healthcheck_path": "/v1/models",
    "llm_sidecar_healthcheck_interval_ms": 1500,
    "llm_bootstrap_install_dir": "~/.poetrymeter/llm",
    "llm_bootstrap_runtime_candidates": ["llama-server", "llamafile"],
    "llm_bootstrap_runtime_url": "",
    "llm_bootstrap_runtime_sha256": "",
    "llm_bootstrap_runtime_filename": "llama-server",
    "llm_bootstrap_model_url": "",
    "llm_bootstrap_model_sha256": "",
    "llm_bootstrap_model_filename": "model.gguf",
    "llm_bootstrap_download_timeout_ms": 120000,
    "llm_bootstrap_overwrite": false,
    "llm_setup_prefer_ollama": true,
    "llm_setup_auto_pull_ollama_model": true,
    "llm_setup_ollama_pull_timeout_ms": 900000
}
```

## Easiest Setup

Run `Poetry Meter: Setup Local LLM` from Command Palette.

The wizard will:
- ask for hardware target (`CPU` or `GPU`)
- ask for quality profile (`Small/Fast` or `Better Accuracy`)
- auto-detect a local runtime (prefers `ollama` by default)
- auto-configure plugin settings
- optionally pull the selected Ollama model
- start the sidecar automatically

If no local runtime is detected, it falls back to bootstrap settings and shows clear guidance.

## Local Sidecar Mode (No Manual Server Command)

Enable sidecar mode to let the plugin launch and manage a local LLM runtime automatically:

```json
{
    "llm_enabled": true,
    "llm_sidecar_auto_start": true,
    "llm_sidecar_binary_path": "/absolute/path/to/llama-server",
    "llm_sidecar_model_path": "/absolute/path/to/model.gguf",
    "llm_model": "local-model"
}
```

Use `llm_sidecar_command_template` if your runtime expects different flags.

## One-Command Onboarding

If runtime/model are not already configured, set download URLs:

```json
{
    "llm_bootstrap_runtime_url": "https://.../llama-server",
    "llm_bootstrap_model_url": "https://.../model.gguf",
    "llm_bootstrap_runtime_sha256": "",
    "llm_bootstrap_model_sha256": ""
}
```

Then run `Poetry Meter: Bootstrap Local LLM`.  
It will:
- auto-detect existing runtime binaries first (`llama-server`, `llamafile`)
- otherwise download runtime/model into `llm_bootstrap_install_dir`
- configure sidecar settings automatically
- attempt to start the sidecar immediately

## Notes

- v1 is tuned for English stress-syllable meter.
- For unknown words, fallback heuristics are used and confidence is reduced.
- Lines above `max_line_length` are skipped for performance.
- LLM mode gracefully falls back to deterministic analysis on sidecar/endpoint failures.
