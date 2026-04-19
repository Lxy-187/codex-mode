# Changelog

## Unreleased

- Added `config`, `help`, and `update` subcommands
- Changed `status` to default to a simple summary
- Added `status --verbose` for detailed diagnostics
- Hid macOS Keychain diagnostics on non-macOS platforms
- Improved config parsing and root-level `openai_base_url` insertion
- Made `update` check local sources first and require `--download` before using the GitHub fallback
- Replaced the old `setup` command with `config`, so URL and API-key inspection and edits happen through one command tree
- Added managed API-key storage with masked display and optional full display
- Fixed Windows config writing to preserve line endings and avoid malformed `config.toml` output
- Changed `api` and `api --relogin` so they do not block on interactive key prompts unless `--prompt` is explicitly passed
- Removed the `relogin` subcommand and moved that behavior to `chatgpt --relogin` and `api --relogin`

## 0.1.0 - 2026-04-19

- Initial cross-platform portable release
- Added shared Python core script for macOS, Linux, and Windows
- Added platform entry wrappers:
  - `codex-mode`
  - `codex-mode.ps1`
  - `codex-mode.cmd`
- Added install scripts:
  - `install.sh`
  - `install.ps1`
  - `install.cmd`
- Added support for:
  - `status`
  - `chatgpt`
  - `api`
- Added release zip packaging and repository-ready metadata
