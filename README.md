# codex-mode portable

Cross-platform Codex auth-mode switcher for macOS, Linux, and Windows.

Requirements:

- Codex CLI installed and available as `codex`, or set `CODEX_BIN`
- Python available on PATH
  - macOS / Linux: `python3`
  - Windows: `python`

Features:

- `status`: show a simple current-mode summary
- `status --verbose`: show base URL, snapshot, and API-key source diagnostics
- `setup`: show platform-aware setup guidance for ChatGPT mode, API mode, URL, and key sources
- `chatgpt`: switch back to saved ChatGPT auth snapshot
- `api`: switch to API-key mode
- `relogin chatgpt`: perform a fresh `codex login` and refresh the ChatGPT snapshot
- `relogin api`: refresh API-key auth using the configured rules
- `update`: update from a local repo, with optional GitHub download fallback
- `help`: show top-level help or help for one subcommand, for example `codex-mode help setup`

API-key lookup order:

1. macOS Keychain service `codex-openai-api-key` if available
2. `OPENAI_API_KEY` environment variable
3. hidden prompt in the terminal

Files managed under `~/.codex`:

- `auth.json`
- `config.toml`
- `auth-profiles/chatgpt.auth.json`
- `auth-profiles/api.auth.json`
- `auth-profiles/api.base_url`

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
./codex-mode setup
./codex-mode help api
./codex-mode help update
./codex-mode chatgpt
./codex-mode api --base-url https://api.xairouter.com
./codex-mode relogin chatgpt
./codex-mode relogin api
./codex-mode update --check
./codex-mode update
./codex-mode update --download
```

Windows direct usage without install:

```powershell
.\codex-mode.ps1 status
.\codex-mode.ps1 api --base-url https://api.xairouter.com
.\codex-mode.ps1 relogin chatgpt
```

```cmd
codex-mode.cmd status
codex-mode.cmd api --base-url https://api.xairouter.com
```

Notes:

- After switching modes in Codex App, fully quit and reopen the app.
- `chatgpt` restores a saved login snapshot. If that snapshot has expired, use `relogin chatgpt`.
- `api` restores a saved API login snapshot if present. Use `relogin api` when the key changes.
- macOS uses Keychain as an optional API-key source. Linux and Windows use `OPENAI_API_KEY` or a hidden prompt.
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

```powershell
$env:CODEX_BIN = "C:\Path\To\codex.exe"
```

Windows usage after install:

PowerShell:

```powershell
$HOME\bin\codex-mode.ps1
$HOME\bin\codex-mode.ps1 api --base-url https://api.xairouter.com
$HOME\bin\codex-mode.ps1 relogin api
```

Command Prompt:

```cmd
%USERPROFILE%\bin\codex-mode.cmd
%USERPROFILE%\bin\codex-mode.cmd chatgpt
%USERPROFILE%\bin\codex-mode.cmd relogin api --base-url https://api.xairouter.com
```

If you add `%USERPROFILE%\bin` to PATH, then on Windows you can run:

```cmd
codex-mode.cmd status
```
