# codex-mode portable

Cross-platform Codex auth-mode switcher for macOS, Linux, and Windows.

Requirements:

- Codex CLI installed and available as `codex`, or set `CODEX_BIN`
- Python available on PATH
  - macOS / Linux: `python3`
  - Windows: `python`

Features:

- `--version` / `-v`: show the installed `codex-mode` version
- `status`: show a simple current-mode summary
- `status --verbose`: show legacy snapshots, API groups, provider config, expected env keys, helper storage, shell env visibility, and GUI env visibility diagnostics
- `chatgpt`: switch back to saved ChatGPT auth snapshot and remove the managed API provider block
- `chatgpt --group NAME`: switch to one named ChatGPT group
- `chatgpt --relogin`: run a fresh ChatGPT login and refresh the selected snapshot
- `chatgpt --relogin --device-auth`: refresh the selected ChatGPT snapshot using `codex login --device-auth`
- `chatgpt --list-groups`: list saved ChatGPT groups
- `chatgpt --set-default-group NAME`: choose which ChatGPT group `codex-mode chatgpt` uses by default
- `chatgpt --show-auth-file`: print the managed auth snapshot path for one ChatGPT group
- `chatgpt --import-auth PATH`: import a user-managed `auth.json` file into one ChatGPT group
- `api`: switch to the default API group using the legacy `auth.json` snapshot flow and `openai_base_url`
- `api --group NAME`: switch to one named API group
- `api --relogin`: force a fresh API login and refresh the selected group's legacy API snapshot
- `api --save-group`: save one group's `base_url` / `env_var` metadata without switching
- `api --list-groups`: list saved API groups
- `api --set-default-group NAME`: choose which API group `codex-mode api` uses by default
- `api --show-auth-file`: print the managed auth snapshot path for one API group
- `api --import-auth PATH`: import a user-managed `auth.json` file into one API group
- `api --provider-mode`: switch to the optional env-driven provider config mode
- `api --show-key`: show the current effective API key in masked form
- `api --show-key-full`: show the full effective API key
- `api --set-key`: save a helper-managed `XAI_API_KEY` value
- `api --prompt-key`: prompt for and save a helper-managed `XAI_API_KEY` value
- `api --clear-key`: clear the helper-managed `XAI_API_KEY` value
- `update`: update from a local repo, with optional GitHub download fallback
- `help`: show top-level help or help for one subcommand, for example `codex-mode help api`
- `release.py`: prepare releases by updating `VERSION`, moving `CHANGELOG` entries, and generating a zip archive

API-key lookup order:

1. macOS Keychain service `codex-openai-api-key` if available
2. Managed file `~/.codex/auth-profiles/api.key`
3. `XAI_API_KEY` environment variable
4. hidden prompt in the terminal

Files managed under `~/.codex`:

- `auth.json`
- `config.toml`
- `auth-profiles/chatgpt.auth.json`
- `auth-profiles/chatgpt.groups.json`
- `auth-profiles/chatgpt.<group>.auth.json`
- `auth-profiles/api.auth.json`
- `auth-profiles/api.base_url`
- `auth-profiles/api.key`
- `auth-profiles/api.groups.json`
- `auth-profiles/api.<group>.auth.json`
- `auth-profiles/api.<group>.base_url`
- `auth-profiles/api.<group>.key`

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

Release workflow:

```bash
python3 ./release.py show
python3 ./release.py prepare 0.1.1
python3 ./release.py package
python3 ./release.py release 0.1.1
python3 ./release.py tag
python3 ./release.py github --draft
python3 ./release.py publish 0.1.1 --draft
```

What it does:

- `show`: prints the current `VERSION`
- `prepare <version>`: updates `VERSION` and moves the current `## Unreleased` section into `## <version> - <today>`
- `package`: creates `dist/codex-mode-v<version>.zip`
- `release <version>`: runs `prepare` and `package` together
- `tag`: creates an annotated git tag like `v0.1.1`
- `github`: uses `gh release create` to publish a GitHub release from the current version and archive
- `publish <version>`: runs `prepare`, `package`, `tag`, and `github` together

Examples:

```bash
./codex-mode
./codex-mode --version
./codex-mode status
./codex-mode status --verbose
./codex-mode help api
./codex-mode help update
./codex-mode chatgpt
./codex-mode chatgpt --group work
./codex-mode chatgpt --relogin
./codex-mode chatgpt --group work --relogin --device-auth
./codex-mode chatgpt --group work --show-auth-file
./codex-mode chatgpt --group work --import-auth ./auth.json
./codex-mode chatgpt --list-groups
./codex-mode chatgpt --set-default-group work
./codex-mode api --base-url https://api.xairouter.com
./codex-mode api --group work
./codex-mode api --relogin
./codex-mode api --group work --base-url https://api.work.example/v1 --env-var WORK_XAI_API_KEY --save-group
./codex-mode api --group work --set-key sk-...
./codex-mode api --set-default-group work
./codex-mode api --list-groups
./codex-mode api --group work --show-auth-file
./codex-mode api --group work --import-auth ./auth.json
./codex-mode api --provider-mode --base-url https://api.xairouter.com
./codex-mode api --relogin --prompt
./codex-mode api --show-key
./codex-mode api --show-key-full
./codex-mode api --set-key sk-...
./codex-mode api --prompt-key
./codex-mode api --clear-key
./codex-mode update --check
./codex-mode update
./codex-mode update --download
python3 ./release.py show
python3 ./release.py prepare 0.1.1
python3 ./release.py package
python3 ./release.py release 0.1.1
python3 ./release.py tag
python3 ./release.py github --draft
python3 ./release.py publish 0.1.1 --draft
```

Windows direct usage without install:

```powershell
.\codex-mode.ps1 status
.\\codex-mode.ps1 chatgpt --group work
.\codex-mode.ps1 api --base-url https://api.xairouter.com
.\\codex-mode.ps1 api --group work --base-url https://api.work.example/v1 --save-group
.\codex-mode.ps1 api --provider-mode --base-url https://api.xairouter.com
.\codex-mode.ps1 chatgpt --relogin
```

```cmd
codex-mode.cmd status
codex-mode.cmd api --base-url https://api.xairouter.com
```

Notes:

- After switching modes in Codex App, fully quit and reopen the app.
- `chatgpt` defaults to one managed ChatGPT group, and ChatGPT groups let you keep multiple ChatGPT `auth.json` snapshots side by side.
- `chatgpt --show-auth-file` prints the exact managed snapshot path so you can inspect or manually edit one ChatGPT group's `auth.json`.
- `chatgpt --import-auth PATH` lets you bring in a hand-managed ChatGPT `auth.json` instead of forcing `codex-mode` to generate it via `codex login`.
- `chatgpt` restores a saved login snapshot. If that snapshot has expired, use `chatgpt --relogin`.
- `api` defaults to the legacy `auth.json` snapshot flow. This is the mode that keeps shared chat history with the app.
- API groups let you keep multiple `base_url`, helper-managed API keys, and legacy `auth.json` snapshots side by side.
- In the default legacy flow, `api` writes `openai_base_url = "..."`, refreshes `auth.json` when needed, and saves the result to the selected group's `auth-profiles/api[.<group>].auth.json`.
- `api --save-group` is the non-destructive way to prepare a group ahead of time without switching away from the current mode.
- `api --show-auth-file` prints the exact managed snapshot path so you can inspect or manually edit one group's `auth.json`.
- `api --import-auth PATH` lets you bring in a hand-managed `auth.json` instead of forcing `codex-mode` to generate it via `codex login --with-api-key`.
- `api --provider-mode` keeps the newer optional provider config block like `model_provider = "xai"` plus `[model_providers.xai]`, `wire_api = "responses"`, `requires_openai_auth = false`, and `env_key = "XAI_API_KEY"`.
- `api` and `api --relogin` do not prompt for an API key by default. Use `api --prompt-key`, `api --set-key`, or pass `--prompt` explicitly.
- `openai_base_url` insertion/removal is idempotent, so repeated `chatgpt` / `api` switches do not accumulate blank lines.
- The managed provider block is also inserted idempotently and removed cleanly when you switch back to `chatgpt`.
- `api --set-key` / `api --prompt-key` save to macOS Keychain by default on macOS, and to group-specific managed key files on Linux or Windows.
- `api --clear-key` only clears the selected helper-managed store. It does not modify `XAI_API_KEY`.
- Each API group may also choose a preferred env var with `--env-var`; if that env var is not set, `codex-mode` still falls back to `XAI_API_KEY` and `OPENAI_API_KEY`.
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
- release packaging excludes `.git`, `__pycache__`, `dist`, `release`, and `*.pyc`
- `release.py github` and `release.py publish` require `gh auth login` first

```powershell
$env:CODEX_BIN = "C:\Path\To\codex.exe"
```

Windows usage after install:

PowerShell:

```powershell
$HOME\bin\codex-mode.ps1
$HOME\bin\codex-mode.ps1 api --base-url https://api.xairouter.com
$HOME\bin\codex-mode.ps1 api --provider-mode --base-url https://api.xairouter.com
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
