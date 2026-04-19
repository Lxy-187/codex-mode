# Changelog

## Unreleased

- Added `setup`, `help`, and `update` subcommands
- Changed `status` to default to a simple summary
- Added `status --verbose` for detailed diagnostics
- Hid macOS Keychain diagnostics on non-macOS platforms
- Improved config parsing and root-level `openai_base_url` insertion

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
  - `relogin chatgpt`
  - `relogin api`
- Added release zip packaging and repository-ready metadata
