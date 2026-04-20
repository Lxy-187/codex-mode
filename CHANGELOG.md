# Changelog

## Unreleased

- Added `help` and `update` subcommands
- Changed `status` to default to a simple summary
- Added `status --verbose` for detailed diagnostics
- Hid macOS Keychain diagnostics on non-macOS platforms
- Improved config parsing and root-level `openai_base_url` insertion
- Made `update` check local sources first and require `--download` before using the GitHub fallback
- Replaced the old `setup` command with a stricter two-mode model: `chatgpt` and `api`
- Added managed API-key storage with masked display and optional full display
- Fixed Windows config writing to preserve line endings and avoid malformed `config.toml` output
- Changed `api` and `api --relogin` so they do not block on interactive key prompts unless `--prompt` is explicitly passed
- Removed the `relogin` subcommand and moved that behavior to `chatgpt --relogin` and `api --relogin`
- Made `openai_base_url` insertion and removal idempotent so repeated mode switches do not accumulate blank lines
- Made API switching atomic when no API key is available, so failed key validation does not write URL, auth, or config files
- Changed API mode to write a managed `model_provider = "xai"` block with `wire_api = "responses"`, `requires_openai_auth = false`, and `env_key = "XAI_API_KEY"`
- Removed the generic `config` subcommand and moved API-key helper management under `api --show-key`, `api --set-key`, `api --prompt-key`, and `api --clear-key`
- Restored the original legacy API mode as the default `api` behavior, using `openai_base_url` plus `auth.json` / `api.auth.json` snapshots for shared chat history
- Kept the newer env-driven provider config as an explicit optional path behind `api --provider-mode`
- Added multiple named API groups with separate `base_url`, helper-managed API keys, and `auth.json` snapshots
- Added API-group management commands for listing groups, selecting the default group, showing/importing auth snapshots, and saving group metadata
- Changed legacy API snapshot handling so the current API group is tracked and restored per group instead of using one shared `api.auth.json`
- Added top-level `--version` / `-v` support and install-time version file copying
- Added `release.py` to manage `VERSION`, roll `CHANGELOG` releases forward, and generate zip archives under `dist/`
- Extended `release.py` with git tag creation and GitHub Release publishing via `gh`

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
