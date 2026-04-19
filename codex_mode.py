#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import io
import json
import locale
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass


KEYCHAIN_SERVICE = "codex-openai-api-key"
REPO_HINT_NAMES = ("codex-mode", "codex-mode-portable")
SOURCE_MARKER = ".codex-mode-source"
GITHUB_REPO = "Lxy-187/codex-mode"
GITHUB_BRANCH = "main"
GITHUB_RELEASE_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_BRANCH_ARCHIVE_URL = f"https://github.com/{GITHUB_REPO}/archive/refs/heads/{GITHUB_BRANCH}.zip"
HTTP_USER_AGENT = "codex-mode-updater"


class CodexModeError(Exception):
    pass


@dataclass
class Paths:
    codex_home: pathlib.Path
    auth_file: pathlib.Path
    config_file: pathlib.Path
    profile_dir: pathlib.Path
    chatgpt_auth_file: pathlib.Path
    api_auth_file: pathlib.Path
    api_base_url_file: pathlib.Path


@dataclass
class ApiKeyInspection:
    platform_name: str
    keychain_supported: bool
    keychain_has_value: bool
    env_var_name: str
    env_var_has_value: bool
    effective_source: str


def build_paths() -> Paths:
    codex_home = pathlib.Path(os.environ.get("CODEX_HOME", pathlib.Path.home() / ".codex")).expanduser()
    return Paths(
        codex_home=codex_home,
        auth_file=codex_home / "auth.json",
        config_file=codex_home / "config.toml",
        profile_dir=codex_home / "auth-profiles",
        chatgpt_auth_file=codex_home / "auth-profiles" / "chatgpt.auth.json",
        api_auth_file=codex_home / "auth-profiles" / "api.auth.json",
        api_base_url_file=codex_home / "auth-profiles" / "api.base_url",
    )


def detect_codex_bin() -> str:
    env_bin = os.environ.get("CODEX_BIN")
    if env_bin:
        return env_bin

    which = shutil.which("codex")
    if which:
        return which

    if platform.system() == "Darwin":
        mac_app_bin = "/Applications/Codex.app/Contents/Resources/codex"
        if pathlib.Path(mac_app_bin).exists():
            return mac_app_bin

    if platform.system() == "Windows":
        which_exe = shutil.which("codex.exe")
        if which_exe:
            return which_exe

    raise CodexModeError("Could not find the Codex CLI. Put `codex` on PATH or set CODEX_BIN.")


def current_platform_name() -> str:
    return platform.system() or "Unknown"


def ensure_profile_dir(paths: Paths) -> None:
    paths.profile_dir.mkdir(parents=True, exist_ok=True)


def read_auth_mode(auth_file: pathlib.Path) -> str:
    if not auth_file.exists():
      return ""
    try:
        data = json.loads(auth_file.read_text())
    except Exception:
        return ""
    return str(data.get("auth_mode", "") or "")


def read_config_text(config_file: pathlib.Path) -> str:
    if not config_file.exists():
        return ""
    raw = config_file.read_bytes()
    encodings = [
        "utf-8",
        "utf-8-sig",
        locale.getpreferredencoding(False) or "utf-8",
        "gb18030",
        "latin-1",
    ]
    seen: set[str] = set()
    for encoding in encodings:
        if encoding in seen:
            continue
        seen.add(encoding)
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def write_config_text(config_file: pathlib.Path, text: str) -> None:
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(text, encoding="utf-8")


def read_openai_base_url(config_file: pathlib.Path) -> str:
    text = read_config_text(config_file)
    match = re.search(r'(?m)^openai_base_url\s*=\s*"([^"]+)"\s*$', text)
    return match.group(1) if match else ""


def remove_openai_base_url(config_file: pathlib.Path) -> None:
    text = read_config_text(config_file)
    new_text = re.sub(r'(?m)^openai_base_url\s*=.*\n?', "", text)
    if new_text != text:
        write_config_text(config_file, new_text)


def set_openai_base_url(config_file: pathlib.Path, base_url: str) -> None:
    text = read_config_text(config_file)
    text = re.sub(r'(?m)^openai_base_url\s*=.*\n?', '', text)
    lines = text.splitlines(keepends=True)
    insert_at = len(lines)
    for idx, line in enumerate(lines):
        if line.lstrip().startswith("["):
            insert_at = idx
            break

    new_lines = lines[:insert_at]
    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] = new_lines[-1] + "\n"
    new_lines.append(f'openai_base_url = "{base_url}"\n')
    if insert_at < len(lines) and new_lines and new_lines[-1].strip():
        new_lines.append("\n")
    new_lines.extend(lines[insert_at:])
    write_config_text(config_file, "".join(new_lines))


def save_current_snapshot(paths: Paths) -> None:
    current_mode = read_auth_mode(paths.auth_file)
    if current_mode == "chatgpt":
        shutil.copy2(paths.auth_file, paths.chatgpt_auth_file)
    elif current_mode == "apikey":
        shutil.copy2(paths.auth_file, paths.api_auth_file)


def require_file(path: pathlib.Path, message: str) -> None:
    if not path.exists():
        raise CodexModeError(message)


def run_codex(codex_bin: str, args: list[str], *, input_text: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [codex_bin, *args],
        input=input_text,
        text=True,
        check=check,
    )


def codex_login_status(codex_bin: str) -> None:
    run_codex(codex_bin, ["login", "status"])


def read_mac_keychain_key() -> str:
    if platform.system() != "Darwin":
        return ""
    if not shutil.which("security"):
        return ""
    proc = subprocess.run(
        ["security", "find-generic-password", "-a", getpass.getuser(), "-s", KEYCHAIN_SERVICE, "-w"],
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def inspect_api_key_sources() -> ApiKeyInspection:
    env_var_name = "OPENAI_API_KEY"
    env_value = os.environ.get(env_var_name, "").strip()
    keychain_value = read_mac_keychain_key()
    platform_name = current_platform_name()
    keychain_supported = platform_name == "Darwin" and shutil.which("security") is not None

    if keychain_value:
        effective_source = "macOS Keychain"
    elif env_value:
        effective_source = env_var_name
    else:
        effective_source = "interactive prompt"

    return ApiKeyInspection(
        platform_name=platform_name,
        keychain_supported=keychain_supported,
        keychain_has_value=bool(keychain_value),
        env_var_name=env_var_name,
        env_var_has_value=bool(env_value),
        effective_source=effective_source,
    )


def resolve_api_key() -> str:
    key = read_mac_keychain_key()
    if key:
        return key

    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key:
        return key

    return getpass.getpass("OpenAI API key: ").strip()


def resolve_base_url(paths: Paths, arg_base_url: str | None) -> str:
    if arg_base_url:
        return arg_base_url
    if paths.api_base_url_file.exists():
        return paths.api_base_url_file.read_text().strip()
    return read_openai_base_url(paths.config_file).strip()


def print_status(paths: Paths, codex_bin: str, *, verbose: bool) -> None:
    auth_mode = read_auth_mode(paths.auth_file)
    config_base_url = read_openai_base_url(paths.config_file)
    saved_api_base_url = paths.api_base_url_file.read_text().strip() if paths.api_base_url_file.exists() else ""
    effective_api_base_url = resolve_base_url(paths, None)
    api_key = inspect_api_key_sources()

    if auth_mode == "chatgpt":
        print("Current mode: ChatGPT", flush=True)
    elif auth_mode == "apikey":
        print("Current mode: API key", flush=True)
    elif auth_mode:
        print(f"Current mode: {auth_mode}", flush=True)
    else:
        print("Current mode: unknown", flush=True)

    if auth_mode == "apikey":
        print(f"Effective API base URL: {effective_api_base_url or 'not set'}", flush=True)

    if verbose:
        print(f"Codex home: {paths.codex_home}", flush=True)
        print(f"Auth file: {'present' if paths.auth_file.exists() else 'missing'}", flush=True)
        print(f"Saved ChatGPT snapshot: {'present' if paths.chatgpt_auth_file.exists() else 'missing'}", flush=True)
        print(f"Saved API snapshot: {'present' if paths.api_auth_file.exists() else 'missing'}", flush=True)

        print(f"Config base URL: {config_base_url or 'not set'}", flush=True)
        print(f"Saved API base URL: {saved_api_base_url or 'not set'}", flush=True)
        print(f"Effective API base URL: {effective_api_base_url or 'not set'}", flush=True)

        print("API key sources:", flush=True)
        if api_key.platform_name == "Darwin":
            print(
                f"  macOS Keychain ({KEYCHAIN_SERVICE}): {'found' if api_key.keychain_has_value else 'not found'}",
                flush=True,
            )
        print(
            f"  Environment variable {api_key.env_var_name}: {'set' if api_key.env_var_has_value else 'not set'}",
            flush=True,
        )
        print("  Interactive prompt: available on demand", flush=True)
        print(f"  Effective source if relogin api runs now: {api_key.effective_source}", flush=True)

    codex_login_status(codex_bin)


def print_setup(paths: Paths) -> None:
    api_key = inspect_api_key_sources()
    effective_api_base_url = resolve_base_url(paths, None)

    print("Codex-mode setup guide", flush=True)
    print("", flush=True)
    print("Purpose", flush=True)
    print("  codex-mode manages three things:", flush=True)
    print("  - your saved ChatGPT login snapshot", flush=True)
    print("  - your saved API-key login snapshot", flush=True)
    print("  - the root-level `openai_base_url` used only in API mode", flush=True)
    print("", flush=True)
    print("Mode model", flush=True)
    print("  - `chatgpt`: use your ChatGPT account session", flush=True)
    print("  - `api`: use `codex login --with-api-key` plus an API-compatible base URL", flush=True)
    print("  - switching modes does not create a fresh login unless you use `relogin`", flush=True)
    print("", flush=True)
    print("Current configuration", flush=True)
    print(f"  - effective API base URL: {effective_api_base_url or 'not set'}", flush=True)
    print(f"  - codex home: {paths.codex_home}", flush=True)
    print("", flush=True)
    print("Common setup flows", flush=True)
    print("  1. Use ChatGPT billing", flush=True)
    print("     - switch to the saved ChatGPT session: `codex-mode chatgpt`", flush=True)
    print("     - if that session is expired: `codex-mode relogin chatgpt`", flush=True)
    print("  2. Use API billing with a custom gateway", flush=True)
    print("     - set or update the base URL and switch mode:", flush=True)
    print("       `codex-mode api --base-url https://api.xairouter.com`", flush=True)
    print("     - if the API key changed, refresh auth: `codex-mode relogin api`", flush=True)
    print("  3. Inspect what is active right now", flush=True)
    print("     - quick summary: `codex-mode status`", flush=True)
    print("     - full diagnostics: `codex-mode status --verbose`", flush=True)
    print("", flush=True)
    print("How to provide the API key", flush=True)
    if api_key.platform_name == "Darwin":
        print("  Preferred order on macOS:", flush=True)
        print(f"  1. macOS Keychain service `{KEYCHAIN_SERVICE}`", flush=True)
        print(
            "     Save once: security add-generic-password -U -a \"$USER\" -s "
            f"{KEYCHAIN_SERVICE} -w 'sk-...'",
            flush=True,
        )
        print("     Read current value: security find-generic-password -a \"$USER\" -s "
              f"{KEYCHAIN_SERVICE} -w", flush=True)
        print(f"  2. Environment variable `{api_key.env_var_name}`", flush=True)
        print("     Temporary shell example: export OPENAI_API_KEY='sk-...'", flush=True)
        print("  3. Interactive prompt if nothing else is configured", flush=True)
    elif api_key.platform_name == "Windows":
        print("  Preferred order on Windows:", flush=True)
        print(f"  1. Environment variable `{api_key.env_var_name}`", flush=True)
        print("     PowerShell example: $env:OPENAI_API_KEY = 'sk-...'", flush=True)
        print("     Persisted example: setx OPENAI_API_KEY \"sk-...\"", flush=True)
        print("  2. Interactive prompt if nothing else is configured", flush=True)
    else:
        print("  Preferred order on Linux:", flush=True)
        print(f"  1. Environment variable `{api_key.env_var_name}`", flush=True)
        print("     Shell example: export OPENAI_API_KEY='sk-...'", flush=True)
        print("  2. Interactive prompt if nothing else is configured", flush=True)
    print("", flush=True)
    print("Examples", flush=True)
    print("  - first-time API setup:", flush=True)
    print("    `codex-mode api --base-url https://api.xairouter.com --refresh-auth`", flush=True)
    print("  - switch back to account billing:", flush=True)
    print("    `codex-mode chatgpt`", flush=True)
    print("  - refresh an expired ChatGPT session:", flush=True)
    print("    `codex-mode relogin chatgpt`", flush=True)
    print("  - refresh API auth after rotating the key:", flush=True)
    print("    `codex-mode relogin api --base-url https://api.xairouter.com`", flush=True)
    print("  - see where the API key would come from right now:", flush=True)
    print("    `codex-mode status --verbose`", flush=True)
    print("", flush=True)
    print("Operational notes", flush=True)
    print("  - `chatgpt` and `api` restore saved snapshots when possible", flush=True)
    print("  - `relogin ...` performs a fresh login and refreshes the saved snapshot", flush=True)
    print("  - after switching modes in Codex App, fully quit and reopen the app", flush=True)
    print("  - `openai_base_url` is written at TOML root level, not inside a marketplace block", flush=True)
    print("", flush=True)
    print("Useful commands", flush=True)
    print("  codex-mode status", flush=True)
    print("  codex-mode status --verbose", flush=True)
    print("  codex-mode setup", flush=True)
    print("  codex-mode help api", flush=True)
    print("  codex-mode help update", flush=True)
    print("  codex-mode chatgpt", flush=True)
    print("  codex-mode api --base-url https://api.xairouter.com", flush=True)
    print("  codex-mode relogin chatgpt", flush=True)
    print("  codex-mode relogin api", flush=True)
    print("  codex-mode update", flush=True)
    print("  codex-mode update --download", flush=True)
    print("", flush=True)
    print("Update behavior", flush=True)
    print("  - `codex-mode update` checks for a usable local repo and updates from it if found", flush=True)
    print("  - if no local repo is found, it stops and tells you how to continue", flush=True)
    print("  - use `codex-mode update --download` to allow a GitHub download fallback", flush=True)
    print(f"  - remote fallback source: {GITHUB_REPO}", flush=True)


def switch_chatgpt(paths: Paths, codex_bin: str) -> None:
    ensure_profile_dir(paths)
    if not paths.auth_file.exists() and not paths.chatgpt_auth_file.exists():
        raise CodexModeError("No Codex auth state found. Run `codex login` first.")

    save_current_snapshot(paths)
    require_file(paths.chatgpt_auth_file, "No saved ChatGPT session snapshot found. Use `relogin chatgpt`.")

    shutil.copy2(paths.chatgpt_auth_file, paths.auth_file)
    remove_openai_base_url(paths.config_file)

    print("Switched Codex to ChatGPT billing mode.", flush=True)
    print("If Codex App is open, fully quit and reopen it.", flush=True)
    codex_login_status(codex_bin)


def switch_api(paths: Paths, codex_bin: str, *, base_url: str | None, refresh_auth: bool) -> None:
    ensure_profile_dir(paths)
    paths.config_file.parent.mkdir(parents=True, exist_ok=True)
    save_current_snapshot(paths)

    final_base_url = resolve_base_url(paths, base_url)
    if not final_base_url:
        raise CodexModeError("No API base URL configured. Pass `--base-url URL`.")

    paths.api_base_url_file.write_text(final_base_url)
    set_openai_base_url(paths.config_file, final_base_url)

    if refresh_auth or not paths.api_auth_file.exists():
        api_key = resolve_api_key()
        if not api_key:
            raise CodexModeError("API key is empty.")
        run_codex(codex_bin, ["login", "--with-api-key"], input_text=api_key)
        shutil.copy2(paths.auth_file, paths.api_auth_file)
    else:
        shutil.copy2(paths.api_auth_file, paths.auth_file)

    print("Switched Codex to API billing mode.", flush=True)
    print(f"Configured openai_base_url = {final_base_url}", flush=True)
    print("If Codex App is open, fully quit and reopen it.", flush=True)
    codex_login_status(codex_bin)


def relogin_chatgpt(paths: Paths, codex_bin: str) -> None:
    ensure_profile_dir(paths)
    remove_openai_base_url(paths.config_file)
    run_codex(codex_bin, ["login"])

    auth_mode = read_auth_mode(paths.auth_file)
    if auth_mode != "chatgpt":
        raise CodexModeError(
            f"Login completed, but the saved auth mode is '{auth_mode or 'unknown'}', not 'chatgpt'."
        )

    shutil.copy2(paths.auth_file, paths.chatgpt_auth_file)
    print("Refreshed ChatGPT login snapshot.", flush=True)
    print("If Codex App is open, fully quit and reopen it.", flush=True)
    codex_login_status(codex_bin)


def is_repo_dir(path: pathlib.Path) -> bool:
    return (path / ".git").exists()


def repo_matches_codex_mode(repo_dir: pathlib.Path) -> bool:
    if not is_repo_dir(repo_dir):
        return False
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_dir), "remote", "get-url", "origin"],
            text=True,
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        return False
    origin = proc.stdout.strip()
    return "Lxy-187/codex-mode" in origin or origin.endswith("/codex-mode") or origin.endswith("/codex-mode.git")


def find_update_repo(explicit_repo: str | None) -> pathlib.Path:
    candidates: list[pathlib.Path] = []

    if explicit_repo:
        candidates.append(pathlib.Path(explicit_repo).expanduser().resolve())

    env_repo = os.environ.get("CODEX_MODE_REPO")
    if env_repo:
        candidates.append(pathlib.Path(env_repo).expanduser().resolve())

    script_dir = pathlib.Path(__file__).resolve().parent
    cwd = pathlib.Path.cwd().resolve()
    marker_file = script_dir / SOURCE_MARKER
    if marker_file.exists():
        marker_text = marker_file.read_text(encoding="utf-8").strip()
        if marker_text:
            candidates.append(pathlib.Path(marker_text).expanduser().resolve())

    def add_with_parents(path: pathlib.Path) -> None:
        current = path
        while True:
            candidates.append(current)
            if current.parent == current:
                break
            current = current.parent

    add_with_parents(script_dir)
    add_with_parents(cwd)

    home = pathlib.Path.home().resolve()
    for name in REPO_HINT_NAMES:
        candidates.append(home / name)

    seen: set[pathlib.Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if repo_matches_codex_mode(candidate):
            return candidate

    raise CodexModeError(
        "Could not find a local codex-mode git repo."
    )


def install_from_directory(source_dir: pathlib.Path, target_dir: pathlib.Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_dir / "codex_mode.py", target_dir / "codex_mode.py")

    system = current_platform_name()
    if system == "Windows":
        shutil.copy2(source_dir / "codex-mode.ps1", target_dir / "codex-mode.ps1")
        shutil.copy2(source_dir / "codex-mode.cmd", target_dir / "codex-mode.cmd")
    else:
        shutil.copy2(source_dir / "codex-mode", target_dir / "codex-mode")
        os.chmod(target_dir / "codex-mode", 0o755)


def install_from_repo(repo_dir: pathlib.Path, target_dir: pathlib.Path) -> None:
    install_from_directory(repo_dir, target_dir)
    (target_dir / SOURCE_MARKER).write_text(str(repo_dir), encoding="utf-8")


def download_url_bytes(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": HTTP_USER_AGENT,
            "Accept": "application/vnd.github+json, application/json",
        },
    )
    with urllib.request.urlopen(req) as resp:
        return resp.read()


def select_release_zip_url() -> str | None:
    try:
        payload = download_url_bytes(GITHUB_RELEASE_API_URL)
        data = json.loads(payload.decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, UnicodeDecodeError):
        return None

    for asset in data.get("assets", []):
        name = str(asset.get("name", ""))
        url = str(asset.get("browser_download_url", ""))
        if name.endswith(".zip") and url:
            return url

    zipball = str(data.get("zipball_url", "") or "")
    return zipball or None


def find_distribution_root(root_dir: pathlib.Path) -> pathlib.Path:
    for candidate in root_dir.rglob("codex_mode.py"):
        parent = candidate.parent
        if (parent / "README.md").exists():
            return parent
    raise CodexModeError("Downloaded archive did not contain a valid codex-mode distribution.")


def update_from_github(target_dir: pathlib.Path) -> None:
    download_url = select_release_zip_url() or GITHUB_BRANCH_ARCHIVE_URL
    print(f"Downloading update from: {download_url}", flush=True)
    archive_bytes = download_url_bytes(download_url)

    with tempfile.TemporaryDirectory(prefix="codex-mode-update-") as tmp:
        tmp_path = pathlib.Path(tmp)
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
            zf.extractall(tmp_path)
        source_dir = find_distribution_root(tmp_path)
        install_from_directory(source_dir, target_dir)
        print(f"Downloaded and reinstalled into: {target_dir}", flush=True)


def update_from_repo(explicit_repo: str | None, *, allow_download: bool, check_only: bool) -> None:
    script_dir = pathlib.Path(__file__).resolve().parent
    target_dir = script_dir
    try:
        repo_dir = find_update_repo(explicit_repo)
    except CodexModeError:
        repo_dir = None

    if repo_dir is not None:
        if check_only:
            print(f"Local repo found: {repo_dir}", flush=True)
            print("Remote download fallback: available but not needed", flush=True)
            return

        subprocess.run(["git", "-C", str(repo_dir), "pull", "--ff-only"], check=True)

        if repo_dir != target_dir:
            install_from_repo(repo_dir, target_dir)
            print(f"Updated from local repo: {repo_dir}", flush=True)
            print(f"Reinstalled into: {target_dir}", flush=True)
        else:
            print(f"Updated repo in place: {repo_dir}", flush=True)
        return

    print("No local codex-mode repo found.", flush=True)
    print(f"Install target: {target_dir}", flush=True)
    if check_only:
        print("Remote download fallback: available", flush=True)
        print("Run `codex-mode update --download` to fetch and reinstall from GitHub.", flush=True)
        return

    if not allow_download:
        raise CodexModeError(
            "No local repo found, so update stopped before downloading. "
            "Run `codex-mode update --check` to inspect sources or `codex-mode update --download` to allow GitHub download."
        )

    print("Proceeding with remote download fallback.", flush=True)
    update_from_github(target_dir)


def build_parser() -> argparse.ArgumentParser:
    description = textwrap.dedent(
        """
        Codex auth-mode manager.

        Use this tool to inspect the current Codex login state, switch between ChatGPT and API-key
        billing modes, refresh expired logins, and update an installed copy from a local repo or GitHub.
        """
    ).strip()
    epilog = textwrap.dedent(
        """
        Common examples:
          codex-mode status
          codex-mode setup
          codex-mode help setup
          codex-mode chatgpt
          codex-mode api --base-url https://api.xairouter.com
          codex-mode relogin chatgpt
          codex-mode relogin api
          codex-mode update
          codex-mode update --check
          codex-mode update --download
          codex-mode update --repo C:\\path\\to\\codex-mode

        API-key lookup order:
          macOS: Keychain -> OPENAI_API_KEY -> interactive prompt
          Windows/Linux: OPENAI_API_KEY -> interactive prompt

        Update strategy:
          1. Check for a local git repo and use `git pull --ff-only` when found
          2. If no local repo is found, stop by default
          3. Use `codex-mode update --download` to allow GitHub fallback
        """
    ).strip()
    parser = argparse.ArgumentParser(
        prog="codex-mode",
        description=description,
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    status_parser = sub.add_parser(
        "status",
        help="Show current mode and login summary",
        description="Show the current Codex auth mode. Use --verbose for base URL, snapshot, and key-source diagnostics.",
    )
    status_parser.add_argument("-v", "--verbose", action="store_true", help="Show detailed diagnostics")
    sub.add_parser(
        "setup",
        help="Show setup instructions for URL and API key configuration",
        description="Print platform-aware setup instructions for ChatGPT mode, API mode, base URL configuration, and API key sources.",
    )
    sub.add_parser(
        "chatgpt",
        help="Switch to the saved ChatGPT login snapshot",
        description="Restore the saved ChatGPT auth snapshot and remove any API-only openai_base_url override.",
    )

    api_parser = sub.add_parser(
        "api",
        help="Switch to API-key mode",
        description="Switch Codex into API-key mode. Optionally set --base-url and use --refresh-auth to force a fresh key read.",
    )
    api_parser.add_argument("--base-url")
    api_parser.add_argument("--refresh-auth", action="store_true")

    relogin_parser = sub.add_parser(
        "relogin",
        help="Refresh the saved ChatGPT or API login snapshot",
        description="Run a fresh login flow and update the stored snapshot for the selected mode.",
    )
    relogin_sub = relogin_parser.add_subparsers(dest="target", required=True)
    relogin_sub.add_parser(
        "chatgpt",
        help="Run codex login for ChatGPT mode",
        description="Remove API-only base URL settings, run `codex login`, and save a fresh ChatGPT snapshot.",
    )
    relogin_api = relogin_sub.add_parser(
        "api",
        help="Refresh API-key auth",
        description="Force a fresh API-key login using the configured source order for your platform.",
    )
    relogin_api.add_argument("--base-url")

    update_parser = sub.add_parser(
        "update",
        help="Update from a local repo, with optional GitHub download fallback",
        description=(
            "Update codex-mode. By default it checks for a local git repo and updates from that repo only. "
            "If no local repo is found, it stops before downloading anything. "
            "Use --download to allow a GitHub fallback through the latest release zip, "
            "or the main-branch source archive if no release zip is available."
        ),
    )
    update_parser.add_argument("--repo")
    update_parser.add_argument("--check", action="store_true", help="Only inspect update sources; do not change anything")
    update_parser.add_argument(
        "--download",
        action="store_true",
        help="Allow a GitHub download fallback when no local repo is found",
    )

    help_parser = sub.add_parser(
        "help",
        help="Show general help or help for a subcommand",
        description="Show the top-level help text or help for one specific subcommand, such as `codex-mode help setup`.",
    )
    help_parser.add_argument("topic", nargs="?")

    parser._subcommand_parsers = {
        "status": status_parser,
        "setup": sub.choices["setup"],
        "chatgpt": sub.choices["chatgpt"],
        "api": api_parser,
        "relogin": relogin_parser,
        "update": update_parser,
        "help": help_parser,
    }

    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    paths = build_paths()
    codex_bin = detect_codex_bin()

    try:
        if args.command in (None, "status"):
            print_status(paths, codex_bin, verbose=getattr(args, "verbose", False))
        elif args.command == "setup":
            print_setup(paths)
        elif args.command == "chatgpt":
            switch_chatgpt(paths, codex_bin)
        elif args.command == "api":
            switch_api(paths, codex_bin, base_url=args.base_url, refresh_auth=args.refresh_auth)
        elif args.command == "relogin":
            if args.target == "chatgpt":
                relogin_chatgpt(paths, codex_bin)
            elif args.target == "api":
                switch_api(paths, codex_bin, base_url=args.base_url, refresh_auth=True)
            else:
                parser.error("unsupported relogin target")
        elif args.command == "update":
            update_from_repo(args.repo, allow_download=args.download, check_only=args.check)
        elif args.command == "help":
            topic = args.topic
            if not topic:
                parser.print_help()
            else:
                if topic == "setup":
                    print_setup(paths)
                    return 0
                subparser = parser._subcommand_parsers.get(topic)
                if subparser is None:
                    raise CodexModeError(f"Unknown help topic: {topic}")
                subparser.print_help()
        else:
            parser.error("unsupported command")
    except CodexModeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        return exc.returncode or 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
