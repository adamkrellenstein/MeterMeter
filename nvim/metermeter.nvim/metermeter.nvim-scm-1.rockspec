local MODREV, SPECREV = "scm", "-1"
rockspec_format = "3.0"
package = "metermeter.nvim"
version = MODREV .. SPECREV

description = {
  summary = "Local, real-time poetic meter annotation for Neovim",
  labels = { "neovim" },
  homepage = "https://github.com/adamkrellenstein/MeterMeter",
  license = "MIT",
}

dependencies = {
  "lua >= 5.1",
}

source = {
  url = "git://github.com/adamkrellenstein/MeterMeter",
}

build = {
  type = "builtin",
  copy_directories = {
    "lua",
    "python",
    "plugin",
    "doc",
    "ftdetect",
  },
}
