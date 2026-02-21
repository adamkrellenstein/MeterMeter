# poetrymeter.nvim

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
  dir = "~/dev/PoetryMeter/nvim/poetrymeter.nvim",
  config = function()
    require("poetrymeter").setup({
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
Plug '~/dev/PoetryMeter/nvim/poetrymeter.nvim'
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

- `:PoetryMeterEnable`
- `:PoetryMeterDisable`
- `:PoetryMeterToggle`
- `:PoetryMeterRescan`
- `:PoetryMeterDump` (writes `/tmp/poetrymeter_nvim_dump.json`)

## File Enable Rules

- `.poem`: enabled by default
- `.typ`: opt-in per file by adding a marker near the top:

```text
// poetrymeter: on
```

For `.typ`, only lines inside `#stanza[ ... ]`, `#couplet[ ... ]`, or `#poem[ ... ]` blocks are annotated.
