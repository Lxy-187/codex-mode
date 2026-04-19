# codex-mode portable

Cross-platform Codex auth-mode switcher for macOS, Linux, and Windows.

Requirements:

- Codex CLI installed and available as `codex`, or set `CODEX_BIN`
- Python available on PATH
  - macOS / Linux: `python3`
  - Windows: `python`

Features:

- `status`: show a simple current-mode summary
- `status --verbose`: show provider config, expected env key, helper storage, shell env visibility, and GUI env visibility diagnostics
- `chatgpt`: switch back to saved ChatGPT auth snapshot and remove the managed API provider block
- `chatgpt --relogin`: run a fresh ChatGPT login and refresh the saved snapshot
- `api`: switch to API-provider mode by writing a managed `model_provider` block
- `api --relogin`: re-validate the API key inputs and rewrite the managed provider block
- `api --show-key`: show the current effective API key in masked form
- `api --show-key-full`: show the full effective API key
- `api --set-key`: save a helper-managed `XAI_API_KEY` value
- `api --prompt-key`: prompt for and save a helper-managed `XAI_API_KEY` value
- `api --clear-key`: clear the helper-managed `XAI_API_KEY` value
- `update`: update from a local repo, with optional GitHub download fallback
- `help`: show top-level help or help for one subcommand, for example `codex-mode help api`

API-key lookup order:

1. macOS Keychain service `codex-openai-api-key` if available
2. Managed file `~/.codex/auth-profiles/api.key`
3. `XAI_API_KEY` environment variable
4. hidden prompt in the terminal

Files managed under `~/.codex`:

- `auth.json`
- `config.toml`
- `auth-profiles/chatgpt.auth.json`
- `auth-profiles/api.base_url`
- `auth-profiles/api.key`

Quick start:

macOS / Linux:

```bash
./install.sh
~/.local/bin/codex-mode status
```

Windows PowerShell:

```powershell
.\install.ps1
$HOME\bin\codex-mode.ps1 status
```

Windows Command Prompt:

```cmd
install.cmd
%USERPROFILE%\bin\codex-mode.cmd status
```

Examples:

```bash
./codex-mode
./codex-mode status
./codex-mode status --verbose
./codex-mode help api
./codex-mode help update
./codex-mode chatgpt
./codex-mode chatgpt --relogin
./codex-mode api --base-url https://api.xairouter.com
./codex-mode api --relogin
./codex-mode api --relogin --prompt
./codex-mode api --show-key
./codex-mode api --show-key-full
./codex-mode api --set-key sk-...
./codex-mode api --prompt-key
./codex-mode api --clear-key
./codex-mode update --check
./codex-mode update
./codex-mode update --download
```

Windows direct usage without install:

```powershell
.\codex-mode.ps1 status
.\codex-mode.ps1 api --base-url https://api.xairouter.com
.\codex-mode.ps1 chatgpt --relogin
```

```cmd
codex-mode.cmd status
codex-mode.cmd api --base-url https://api.xairouter.com
```

Notes:

- After switching modes in Codex App, fully quit and reopen the app.
- `chatgpt` restores a saved login snapshot. If that snapshot has expired, use `chatgpt --relogin`.
- `api` writes a managed config block like `model_provider = "xai"` plus `[model_providers.xai]`, `wire_api = "responses"`, `requires_openai_auth = false`, and `env_key = "XAI_API_KEY"`.
- `api` and `api --relogin` do not prompt for an API key by default. Use `api --prompt-key`, `api --set-key`, or pass `--prompt` explicitly.
- The managed provider block is inserted idempotently and removed cleanly when you switch back to `chatgpt`.
- `api --set-key` / `api --prompt-key` save to macOS Keychain by default on macOS, and to `~/.codex/auth-profiles/api.key` on Linux or Windows.
- `api --clear-key` only clears the selected helper-managed store. It does not modify `XAI_API_KEY`.
- `status --verbose` tells you three separate things:
  - which env key the provider expects
  - whether local helper storage exists
  - whether the current shell or GUI session can actually read `XAI_API_KEY`
- If `codex` is not on PATH, set `CODEX_BIN` before running. Example on Windows PowerShell:
- `update` works best when:
  - you run it inside the cloned repo, or
  - you installed via the bundled install script, which writes a source marker file, or
  - you pass `codex-mode update --repo <path>`
- `update` behavior:
  - `codex-mode update --check` only inspects the available update path
  - `codex-mode update` updates from a local repo when one is found
  - if no local repo is found, it stops before downloading anything
  - use `codex-mode update --download` to allow a GitHub download and reinstall fallback
- after installation on a new device, prefer `codex-mode update` over manually re-running `git pull` and `install.ps1` / `install.sh`

```powershell
$env:CODEX_BIN = "C:\Path\To\codex.exe"
```

Windows usage after install:

PowerShell:

```powershell
$HOME\bin\codex-mode.ps1
$HOME\bin\codex-mode.ps1 api --base-url https://api.xairouter.com
$HOME\bin\codex-mode.ps1 api --relogin
```

Command Prompt:

```cmd
%USERPROFILE%\bin\codex-mode.cmd
%USERPROFILE%\bin\codex-mode.cmd chatgpt
%USERPROFILE%\bin\codex-mode.cmd api --relogin --base-url https://api.xairouter.com
```

If you add `%USERPROFILE%\bin` to PATH, then on Windows you can run:

```cmd
codex-mode.cmd status
```
