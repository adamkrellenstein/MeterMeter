# metermeter.nvim

Local, real-time poetic meter annotation for Neovim.

This is a Neovim-oriented implementation of the same core idea as the Sublime Text package in this repo.

## Requirements

- Neovim 0.10+ (uses `vim.system()` and `vim.json`).
- Python 3.8+ (`python3` on PATH).
- Optional (for LLM refinement): Ollama running locally, or any OpenAI-compatible `chat/completions` endpoint.

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

## File Enable Rules

MeterMeter enables itself when `&filetype` includes `metermeter`.

- `*.poem` is detected automatically via `ftdetect` (filetype becomes `metermeter`).
- Mixed-format files can opt-in via a modeline, for example:

```text
vim: set ft=typst.metermeter :
```

By default, MeterMeter annotates every non-comment line.

If you want an explicit "poetry line marker" for mixed-format files, set:

```lua
require("metermeter").setup({ require_trailing_backslash = true })
```

Then MeterMeter will only annotate lines that end with a trailing backslash (`\`).

Comment lines are ignored using the buffer's native `&comments` / `&commentstring`.
