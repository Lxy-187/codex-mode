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
    api_key_file: pathlib.Path


@dataclass
class ApiKeyInspection:
    platform_name: str
    keychain_supported: bool
    keychain_has_value: bool
    file_has_value: bool
    env_var_name: str
    env_var_has_value: bool
    effective_source: str
    effective_value: str


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
        api_key_file=codex_home / "auth-profiles" / "api.key",
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
    with config_file.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def write_secret_text(secret_file: pathlib.Path, value: str, *, newline: str = "\n") -> None:
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    with secret_file.open("w", encoding="utf-8", newline="") as fh:
        fh.write(value.strip() + newline)
    if os.name != "nt":
        os.chmod(secret_file, 0o600)


def read_secret_text(secret_file: pathlib.Path) -> str:
    if not secret_file.exists():
        return ""
    return secret_file.read_text(encoding="utf-8").strip()


def remove_secret_file(secret_file: pathlib.Path) -> None:
    if secret_file.exists():
        secret_file.unlink()


def read_openai_base_url(config_file: pathlib.Path) -> str:
    text = read_config_text(config_file)
    match = re.search(r'(?m)^openai_base_url\s*=\s*"([^"]+)"\s*$', text)
    return match.group(1) if match else ""


def detect_newline(text: str) -> str:
    if "\r\n" in text:
        return "\r\n"
    return "\n"


def remove_openai_base_url(config_file: pathlib.Path) -> None:
    text = read_config_text(config_file)
    new_text = re.sub(r'(?m)^openai_base_url\s*=.*(?:\r?\n)?', "", text)
    if new_text != text:
        write_config_text(config_file, new_text)


def set_openai_base_url(config_file: pathlib.Path, base_url: str) -> None:
    text = read_config_text(config_file)
    newline = detect_newline(text)
    text = re.sub(r'(?m)^openai_base_url\s*=.*(?:\r?\n)?', '', text)
    lines = text.splitlines(keepends=True)
    insert_at = len(lines)
    for idx, line in enumerate(lines):
        if line.lstrip().startswith("["):
            insert_at = idx
            break

    new_lines = lines[:insert_at]
    if new_lines and not new_lines[-1].endswith(("\n", "\r\n")):
        new_lines[-1] = new_lines[-1] + newline
    new_lines.append(f'openai_base_url = "{base_url}"{newline}')
    if insert_at < len(lines) and new_lines and new_lines[-1].strip():
        new_lines.append(newline)
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


def write_mac_keychain_key(value: str) -> None:
    if platform.system() != "Darwin":
        raise CodexModeError("macOS Keychain is only available on macOS.")
    if not shutil.which("security"):
        raise CodexModeError("Could not find the macOS `security` tool.")
    proc = subprocess.run(
        ["security", "add-generic-password", "-U", "-a", getpass.getuser(), "-s", KEYCHAIN_SERVICE, "-w", value],
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise CodexModeError(proc.stderr.strip() or "Failed to write the API key to macOS Keychain.")


def remove_mac_keychain_key() -> None:
    if platform.system() != "Darwin":
        raise CodexModeError("macOS Keychain is only available on macOS.")
    if not shutil.which("security"):
        raise CodexModeError("Could not find the macOS `security` tool.")
    proc = subprocess.run(
        ["security", "delete-generic-password", "-a", getpass.getuser(), "-s", KEYCHAIN_SERVICE],
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0 and "could not be found" not in proc.stderr.lower():
        raise CodexModeError(proc.stderr.strip() or "Failed to remove the API key from macOS Keychain.")


def read_managed_api_key(paths: Paths) -> str:
    return read_secret_text(paths.api_key_file)


def mask_secret(value: str) -> str:
    if not value:
        return "not set"
    if len(value) <= 8:
        if len(value) <= 2:
            return "*" * len(value)
        return value[:1] + "*" * (len(value) - 2) + value[-1:]
    return value[:4] + "*" * (len(value) - 8) + value[-4:]


def inspect_api_key_sources(paths: Paths) -> ApiKeyInspection:
    env_var_name = "OPENAI_API_KEY"
    env_value = os.environ.get(env_var_name, "").strip()
    keychain_value = read_mac_keychain_key()
    file_value = read_managed_api_key(paths)
    platform_name = current_platform_name()
    keychain_supported = platform_name == "Darwin" and shutil.which("security") is not None

    if keychain_value:
        effective_source = "macOS Keychain"
        effective_value = keychain_value
    elif file_value:
        effective_source = "managed file"
        effective_value = file_value
    elif env_value:
        effective_source = env_var_name
        effective_value = env_value
    else:
        effective_source = "interactive prompt"
        effective_value = ""

    return ApiKeyInspection(
        platform_name=platform_name,
        keychain_supported=keychain_supported,
        keychain_has_value=bool(keychain_value),
        file_has_value=bool(file_value),
        env_var_name=env_var_name,
        env_var_has_value=bool(env_value),
        effective_source=effective_source,
        effective_value=effective_value,
    )


def resolve_api_key(paths: Paths, *, allow_prompt: bool) -> str:
    key = read_mac_keychain_key()
    if key:
        return key

    key = read_managed_api_key(paths)
    if key:
        return key

    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key:
        return key

    if allow_prompt:
        return getpass.getpass("OpenAI API key: ").strip()

    return ""


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
    api_key = inspect_api_key_sources(paths)

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
            f"  Managed file ({paths.api_key_file}): {'set' if api_key.file_has_value else 'not set'}",
            flush=True,
        )
        print(
            f"  Environment variable {api_key.env_var_name}: {'set' if api_key.env_var_has_value else 'not set'}",
            flush=True,
        )
        print("  Interactive prompt: available on demand", flush=True)
        print(f"  Effective source if relogin api runs now: {api_key.effective_source}", flush=True)

    codex_login_status(codex_bin)


def print_config_list(paths: Paths) -> None:
    auth_mode = read_auth_mode(paths.auth_file) or "unknown"
    config_base_url = read_openai_base_url(paths.config_file)
    saved_api_base_url = read_secret_text(paths.api_base_url_file)
    effective_api_base_url = resolve_base_url(paths, None)
    api_key = inspect_api_key_sources(paths)

    print("Codex-mode configuration", flush=True)
    print(f"Current mode: {auth_mode}", flush=True)
    print(f"Codex home: {paths.codex_home}", flush=True)
    print("", flush=True)
    print("Base URL", flush=True)
    print(f"  Config file value: {config_base_url or 'not set'}", flush=True)
    print(f"  Saved API value: {saved_api_base_url or 'not set'}", flush=True)
    print(f"  Effective API value: {effective_api_base_url or 'not set'}", flush=True)
    print("", flush=True)
    print("API key", flush=True)
    print(f"  Effective source: {api_key.effective_source}", flush=True)
    print(f"  Effective value: {mask_secret(api_key.effective_value)}", flush=True)
    if api_key.platform_name == "Darwin":
        print(
            f"  macOS Keychain ({KEYCHAIN_SERVICE}): {'set' if api_key.keychain_has_value else 'not set'}",
            flush=True,
        )
    print(f"  Managed file ({paths.api_key_file}): {'set' if api_key.file_has_value else 'not set'}", flush=True)
    print(
        f"  Environment variable {api_key.env_var_name}: {'set' if api_key.env_var_has_value else 'not set'}",
        flush=True,
    )


def show_base_url_config(paths: Paths) -> None:
    auth_mode = read_auth_mode(paths.auth_file)
    saved_api_base_url = read_secret_text(paths.api_base_url_file)
    effective_api_base_url = resolve_base_url(paths, None)

    print(f"Saved API base URL: {saved_api_base_url or 'not set'}", flush=True)
    print(f"Effective API base URL: {effective_api_base_url or 'not set'}", flush=True)
    if auth_mode == "apikey":
        print("Current mode uses the API base URL now.", flush=True)
    else:
        print("Current mode is not API. The saved value will apply on the next API switch.", flush=True)


def set_base_url_config(paths: Paths, base_url: str) -> None:
    normalized = base_url.strip()
    if not normalized:
        raise CodexModeError("Base URL is empty.")

    write_secret_text(paths.api_base_url_file, normalized)
    if read_auth_mode(paths.auth_file) == "apikey":
        set_openai_base_url(paths.config_file, normalized)

    print(f"Saved API base URL: {normalized}", flush=True)
    if read_auth_mode(paths.auth_file) == "apikey":
        print("Applied the new base URL to the active API mode config.", flush=True)
    else:
        print("Saved for the next API mode switch. ChatGPT mode was left unchanged.", flush=True)


def unset_base_url_config(paths: Paths) -> None:
    remove_secret_file(paths.api_base_url_file)
    remove_openai_base_url(paths.config_file)
    print("Cleared the saved API base URL.", flush=True)


def default_api_key_store() -> str:
    if current_platform_name() == "Darwin" and shutil.which("security") is not None:
        return "keychain"
    return "file"


def resolve_api_key_store(store: str) -> str:
    resolved = default_api_key_store() if store == "auto" else store
    if resolved == "keychain" and current_platform_name() != "Darwin":
        raise CodexModeError("The `keychain` store is only available on macOS.")
    return resolved


def show_api_key_config(paths: Paths, *, show_full: bool) -> None:
    inspection = inspect_api_key_sources(paths)
    print(f"Effective source: {inspection.effective_source}", flush=True)
    if inspection.effective_value:
        value = inspection.effective_value if show_full else mask_secret(inspection.effective_value)
        print(f"Effective API key: {value}", flush=True)
    else:
        print("Effective API key: not set", flush=True)


def set_api_key_config(paths: Paths, *, api_key: str, store: str) -> None:
    normalized = api_key.strip()
    if not normalized:
        raise CodexModeError("API key is empty.")

    resolved_store = resolve_api_key_store(store)
    if resolved_store == "keychain":
        write_mac_keychain_key(normalized)
        print("Saved the API key to macOS Keychain.", flush=True)
    elif resolved_store == "file":
        write_secret_text(paths.api_key_file, normalized)
        print(f"Saved the API key to the managed file: {paths.api_key_file}", flush=True)
    else:
        raise CodexModeError(f"Unsupported API-key store: {resolved_store}")

    print(f"Stored API key: {mask_secret(normalized)}", flush=True)


def clear_api_key_config(paths: Paths, *, store: str) -> None:
    resolved_store = resolve_api_key_store(store)
    if resolved_store == "keychain":
        remove_mac_keychain_key()
        print("Cleared the API key from macOS Keychain.", flush=True)
    elif resolved_store == "file":
        remove_secret_file(paths.api_key_file)
        print(f"Cleared the managed API key file: {paths.api_key_file}", flush=True)
    else:
        raise CodexModeError(f"Unsupported API-key store: {resolved_store}")
    print("Environment variables are not modified by codex-mode.", flush=True)


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


def switch_api(
    paths: Paths,
    codex_bin: str,
    *,
    base_url: str | None,
    refresh_auth: bool,
    prompt_for_key: bool,
) -> None:
    ensure_profile_dir(paths)
    paths.config_file.parent.mkdir(parents=True, exist_ok=True)
    save_current_snapshot(paths)

    final_base_url = resolve_base_url(paths, base_url)
    if not final_base_url:
        raise CodexModeError("No API base URL configured. Pass `--base-url URL`.")

    write_secret_text(paths.api_base_url_file, final_base_url)
    set_openai_base_url(paths.config_file, final_base_url)

    if refresh_auth or not paths.api_auth_file.exists():
        api_key = resolve_api_key(paths, allow_prompt=prompt_for_key)
        if not api_key:
            raise CodexModeError(
                "No API key is currently available from managed storage or environment variables. "
                "Use `codex-mode config api-key --prompt`, `codex-mode config api-key --set ...`, "
                "or rerun with `--prompt`."
            )
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


def handle_config_command(paths: Paths, args: argparse.Namespace) -> None:
    if getattr(args, "list", False):
        print_config_list(paths)
        return

    if args.config_target in (None, "base-url"):
        if args.config_target is None:
            print_config_list(paths)
            return
        if args.set is not None:
            set_base_url_config(paths, args.set)
        elif args.unset:
            unset_base_url_config(paths)
        else:
            show_base_url_config(paths)
        return

    if args.config_target == "api-key":
        if args.set is not None:
            set_api_key_config(paths, api_key=args.set, store=args.store)
        elif args.prompt:
            set_api_key_config(paths, api_key=getpass.getpass("OpenAI API key: "), store=args.store)
        elif args.clear:
            clear_api_key_config(paths, store=args.store)
        else:
            show_api_key_config(paths, show_full=args.show_full)
        return

    raise CodexModeError("Unsupported config target.")


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
          codex-mode config --list
          codex-mode config base-url --set https://api.xairouter.com
          codex-mode config api-key
          codex-mode config api-key --show-full
          codex-mode config api-key --prompt
          codex-mode chatgpt
          codex-mode api --base-url https://api.xairouter.com
          codex-mode api --base-url https://api.xairouter.com --prompt
          codex-mode relogin chatgpt
          codex-mode relogin api
          codex-mode relogin api --prompt
          codex-mode update
          codex-mode update --check
          codex-mode update --download
          codex-mode update --repo C:\\path\\to\\codex-mode

        API-key lookup order:
          macOS: Keychain -> managed file -> OPENAI_API_KEY -> interactive prompt
          Windows/Linux: managed file -> OPENAI_API_KEY -> interactive prompt

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
    config_parser = sub.add_parser(
        "config",
        help="View or modify the saved API base URL and API key settings",
        description=(
            "Inspect or change codex-mode configuration. Use `config --list` for a full summary, "
            "`config base-url` to manage the saved API base URL, and `config api-key` to inspect or update the API key."
        ),
    )
    config_parser.add_argument("--list", action="store_true", help="Show the full configuration summary")
    config_sub = config_parser.add_subparsers(dest="config_target")

    config_base_url = config_sub.add_parser(
        "base-url",
        help="Show or change the saved API base URL",
        description="Show the saved API base URL, set it, or clear it.",
    )
    config_base_url_group = config_base_url.add_mutually_exclusive_group()
    config_base_url_group.add_argument("--set", metavar="URL", help="Save this URL for API mode")
    config_base_url_group.add_argument("--unset", action="store_true", help="Clear the saved API base URL")

    config_api_key = config_sub.add_parser(
        "api-key",
        help="Show or change the API key used by relogin/api mode",
        description=(
            "Show the current effective API key source and value, or save/clear a managed API key. "
            "By default the value is masked."
        ),
    )
    config_api_key_group = config_api_key.add_mutually_exclusive_group()
    config_api_key_group.add_argument("--set", metavar="KEY", help="Save this API key into the selected store")
    config_api_key_group.add_argument("--prompt", action="store_true", help="Prompt securely for an API key and save it")
    config_api_key_group.add_argument("--clear", action="store_true", help="Clear the selected managed API-key store")
    config_api_key_group.add_argument("--show-full", action="store_true", help="Print the full effective API key")
    config_api_key.add_argument(
        "--store",
        choices=["auto", "keychain", "file"],
        default="auto",
        help="Select where to save or clear the managed API key",
    )
    sub.add_parser(
        "chatgpt",
        help="Switch to the saved ChatGPT login snapshot",
        description="Restore the saved ChatGPT auth snapshot and remove any API-only openai_base_url override.",
    )

    api_parser = sub.add_parser(
        "api",
        help="Switch to API-key mode",
        description=(
            "Switch Codex into API-key mode. Optionally set --base-url and use --refresh-auth to force a fresh key read. "
            "By default this command does not prompt for an API key."
        ),
    )
    api_parser.add_argument("--base-url")
    api_parser.add_argument("--refresh-auth", action="store_true")
    api_parser.add_argument("--prompt", action="store_true", help="Allow a secure prompt for the API key if no stored key is available")

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
        description=(
            "Force a fresh API-key login using the configured source order for your platform. "
            "By default this command does not prompt for an API key."
        ),
    )
    relogin_api.add_argument("--base-url")
    relogin_api.add_argument("--prompt", action="store_true", help="Allow a secure prompt for the API key if no stored key is available")

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
        description="Show the top-level help text or help for one specific subcommand, such as `codex-mode help config`.",
    )
    help_parser.add_argument("topic", nargs="?")

    parser._subcommand_parsers = {
        "status": status_parser,
        "config": config_parser,
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
        elif args.command == "config":
            handle_config_command(paths, args)
        elif args.command == "chatgpt":
            switch_chatgpt(paths, codex_bin)
        elif args.command == "api":
            switch_api(
                paths,
                codex_bin,
                base_url=args.base_url,
                refresh_auth=args.refresh_auth,
                prompt_for_key=args.prompt,
            )
        elif args.command == "relogin":
            if args.target == "chatgpt":
                relogin_chatgpt(paths, codex_bin)
            elif args.target == "api":
                switch_api(
                    paths,
                    codex_bin,
                    base_url=args.base_url,
                    refresh_auth=True,
                    prompt_for_key=args.prompt,
                )
            else:
                parser.error("unsupported relogin target")
        elif args.command == "update":
            update_from_repo(args.repo, allow_download=args.download, check_only=args.check)
        elif args.command == "help":
            topic = args.topic
            if not topic:
                parser.print_help()
            else:
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
