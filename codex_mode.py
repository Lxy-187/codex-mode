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
API_PROVIDER_ID = "xai"
API_PROVIDER_NAME = "OpenAI"
API_PROVIDER_WIRE_API = "responses"
API_PROVIDER_ENV_KEY = "XAI_API_KEY"
API_PROVIDER_REQUIRES_OPENAI_AUTH = "false"
API_CONFIG_START_COMMENT = "# codex-mode api config start"
API_CONFIG_END_COMMENT = "# codex-mode api config end"


class CodexModeError(Exception):
    pass


@dataclass
class Paths:
    codex_home: pathlib.Path
    auth_file: pathlib.Path
    config_file: pathlib.Path
    profile_dir: pathlib.Path
    chatgpt_auth_file: pathlib.Path
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
    gui_env_var_has_value: bool
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


def is_blank_line(line: str) -> bool:
    return line.strip() == ""


def is_table_header_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("[") and stripped.endswith("]")


def compact_leading_blank_lines(lines: list[str]) -> list[str]:
    idx = 0
    while idx < len(lines) and is_blank_line(lines[idx]):
        idx += 1
    return lines[idx:]


def collapse_consecutive_blank_lines(lines: list[str]) -> list[str]:
    collapsed: list[str] = []
    previous_blank = False
    for line in lines:
        blank = is_blank_line(line)
        if blank and previous_blank:
            continue
        collapsed.append(line)
        previous_blank = blank
    return compact_leading_blank_lines(collapsed)


def read_model_provider(config_file: pathlib.Path) -> str:
    text = read_config_text(config_file)
    match = re.search(r'(?m)^model_provider\s*=\s*"([^"]+)"\s*$', text)
    return match.group(1) if match else ""


def read_provider_base_url(config_file: pathlib.Path, provider_id: str) -> str:
    text = read_config_text(config_file)
    pattern = (
        rf"(?ms)^\[model_providers\.{re.escape(provider_id)}\]\s*$"
        rf"(.*?)(?=^\[|\Z)"
    )
    match = re.search(pattern, text)
    if not match:
        return ""
    section_body = match.group(1)
    value_match = re.search(r'(?m)^\s*base_url\s*=\s*"([^"]+)"\s*$', section_body)
    return value_match.group(1) if value_match else ""


def api_provider_config_is_active(config_file: pathlib.Path) -> bool:
    text = read_config_text(config_file)
    return (
        API_CONFIG_START_COMMENT in text
        or read_model_provider(config_file) == API_PROVIDER_ID
        or bool(read_provider_base_url(config_file, API_PROVIDER_ID))
    )


def render_api_provider_block(base_url: str, newline: str) -> list[str]:
    return [
        f"{API_CONFIG_START_COMMENT}{newline}",
        f'model_provider = "{API_PROVIDER_ID}"{newline}',
        newline,
        f"[model_providers.{API_PROVIDER_ID}]{newline}",
        f'name = "{API_PROVIDER_NAME}"{newline}',
        f'base_url = "{base_url}"{newline}',
        f'wire_api = "{API_PROVIDER_WIRE_API}"{newline}',
        f"requires_openai_auth = {API_PROVIDER_REQUIRES_OPENAI_AUTH}{newline}",
        f'env_key = "{API_PROVIDER_ENV_KEY}"{newline}',
        f"{API_CONFIG_END_COMMENT}{newline}",
    ]


def remove_openai_base_url(config_file: pathlib.Path) -> None:
    text = read_config_text(config_file)
    lines = text.splitlines(keepends=True)
    removed = False
    new_lines: list[str] = []

    for line in lines:
        if re.match(r"^\s*openai_base_url\s*=", line):
            removed = True
            continue
        new_lines.append(line)

    if not removed:
        return

    write_config_text(config_file, "".join(collapse_consecutive_blank_lines(new_lines)))


def set_openai_base_url(config_file: pathlib.Path, base_url: str) -> None:
    text = read_config_text(config_file)
    newline = detect_newline(text)
    original_lines = text.splitlines(keepends=True)
    lines = [
        line
        for line in original_lines
        if not re.match(r"^\s*openai_base_url\s*=", line)
    ]

    lines = compact_leading_blank_lines(lines)
    insert_at = len(lines)
    for idx, line in enumerate(lines):
        if is_blank_line(line) or is_table_header_line(line):
            insert_at = idx
            break

    before = lines[:insert_at]
    after = compact_leading_blank_lines(lines[insert_at:])

    new_lines = before
    if new_lines and not new_lines[-1].endswith(("\n", "\r\n")):
        new_lines[-1] = new_lines[-1] + newline

    new_lines.append(f'openai_base_url = "{base_url}"{newline}')
    if after:
        new_lines.append(newline)
    new_lines.extend(after)
    write_config_text(config_file, "".join(collapse_consecutive_blank_lines(new_lines)))


def remove_api_provider_config(config_file: pathlib.Path) -> None:
    text = read_config_text(config_file)
    lines = text.splitlines(keepends=True)
    new_lines: list[str] = []
    in_managed_block = False
    in_provider_section = False
    removed = False

    for line in lines:
        stripped = line.strip()

        if stripped == API_CONFIG_START_COMMENT:
            in_managed_block = True
            removed = True
            continue
        if in_managed_block:
            removed = True
            if stripped == API_CONFIG_END_COMMENT:
                in_managed_block = False
            continue

        if re.match(rf'^\s*model_provider\s*=\s*"{re.escape(API_PROVIDER_ID)}"\s*$', line):
            removed = True
            continue

        if re.match(rf'^\[model_providers\.{re.escape(API_PROVIDER_ID)}\]\s*$', stripped):
            in_provider_section = True
            removed = True
            continue
        if in_provider_section:
            if is_table_header_line(line):
                in_provider_section = False
            else:
                removed = True
                continue

        if in_provider_section:
            removed = True
            continue

        new_lines.append(line)

    if not removed:
        return

    write_config_text(config_file, "".join(collapse_consecutive_blank_lines(new_lines)))


def set_api_provider_config(config_file: pathlib.Path, base_url: str) -> None:
    text = read_config_text(config_file)
    newline = detect_newline(text)
    remove_api_provider_config(config_file)
    remove_openai_base_url(config_file)
    text = read_config_text(config_file)
    lines = collapse_consecutive_blank_lines(text.splitlines(keepends=True))

    insert_at = len(lines)
    for idx, line in enumerate(lines):
        if is_blank_line(line) or is_table_header_line(line):
            insert_at = idx
            break

    before = lines[:insert_at]
    after = compact_leading_blank_lines(lines[insert_at:])
    new_lines = before

    if new_lines and not new_lines[-1].endswith(("\n", "\r\n")):
        new_lines[-1] = new_lines[-1] + newline
    if new_lines and not is_blank_line(new_lines[-1]):
        new_lines.append(newline)

    new_lines.extend(render_api_provider_block(base_url, newline))
    if after:
        new_lines.append(newline)
    new_lines.extend(after)

    write_config_text(config_file, "".join(collapse_consecutive_blank_lines(new_lines)))


def save_current_snapshot(paths: Paths) -> None:
    current_mode = read_auth_mode(paths.auth_file)
    if current_mode == "chatgpt":
        shutil.copy2(paths.auth_file, paths.chatgpt_auth_file)


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


def read_launchctl_env(var_name: str) -> str:
    if platform.system() != "Darwin":
        return ""
    if not shutil.which("launchctl"):
        return ""
    proc = subprocess.run(
        ["launchctl", "getenv", var_name],
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
    env_var_name = API_PROVIDER_ENV_KEY
    env_value = os.environ.get(env_var_name, "").strip()
    gui_env_value = read_launchctl_env(env_var_name)
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
        gui_env_var_has_value=bool(gui_env_value),
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

    key = os.environ.get(API_PROVIDER_ENV_KEY, "").strip()
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
    provider_base_url = read_provider_base_url(paths.config_file, API_PROVIDER_ID).strip()
    if provider_base_url:
        return provider_base_url
    return read_openai_base_url(paths.config_file).strip()


def print_status(paths: Paths, codex_bin: str, *, verbose: bool) -> None:
    auth_mode = read_auth_mode(paths.auth_file)
    api_mode_active = api_provider_config_is_active(paths.config_file)
    config_base_url = read_openai_base_url(paths.config_file)
    provider_base_url = read_provider_base_url(paths.config_file, API_PROVIDER_ID)
    saved_api_base_url = paths.api_base_url_file.read_text().strip() if paths.api_base_url_file.exists() else ""
    effective_api_base_url = resolve_base_url(paths, None)
    api_key = inspect_api_key_sources(paths)

    if api_mode_active:
        print("Current mode: API key", flush=True)
    elif auth_mode == "apikey":
        print("Current mode: legacy API auth", flush=True)
    elif auth_mode == "chatgpt":
        print("Current mode: ChatGPT", flush=True)
    elif auth_mode:
        print(f"Current mode: {auth_mode}", flush=True)
    else:
        print("Current mode: unknown", flush=True)

    if api_mode_active:
        print(f"Effective API base URL: {effective_api_base_url or 'not set'}", flush=True)

    if verbose:
        print(f"Codex home: {paths.codex_home}", flush=True)
        print(f"Auth file: {'present' if paths.auth_file.exists() else 'missing'}", flush=True)
        print(f"Saved ChatGPT snapshot: {'present' if paths.chatgpt_auth_file.exists() else 'missing'}", flush=True)
        print(f"API provider config active: {'yes' if api_mode_active else 'no'}", flush=True)
        if auth_mode == "apikey" and not api_mode_active:
            print("Legacy API auth state detected: yes", flush=True)

        print(f"Config base URL: {config_base_url or 'not set'}", flush=True)
        print(f"Provider base URL: {provider_base_url or 'not set'}", flush=True)
        print(f"Saved API base URL: {saved_api_base_url or 'not set'}", flush=True)
        print(f"Effective API base URL: {effective_api_base_url or 'not set'}", flush=True)
        print(f"Model provider: {read_model_provider(paths.config_file) or 'not set'}", flush=True)
        print("", flush=True)
        print("Provider auth model:", flush=True)
        print(f"  Expected provider env key: {API_PROVIDER_ENV_KEY}", flush=True)
        print(f"  requires_openai_auth: {API_PROVIDER_REQUIRES_OPENAI_AUTH}", flush=True)
        print("Local helper availability:", flush=True)
        if api_key.platform_name == "Darwin":
            print(
                f"  macOS Keychain ({KEYCHAIN_SERVICE}): {'set' if api_key.keychain_has_value else 'not set'}",
                flush=True,
            )
        print(
            f"  Managed file ({paths.api_key_file}): {'set' if api_key.file_has_value else 'not set'}",
            flush=True,
        )
        print("Environment visibility:", flush=True)
        print(
            f"  Current shell {api_key.env_var_name}: {'set' if api_key.env_var_has_value else 'not set'}",
            flush=True,
        )
        if api_key.platform_name == "Darwin":
            print(
                f"  GUI session {api_key.env_var_name} via launchctl: {'set' if api_key.gui_env_var_has_value else 'not set'}",
                flush=True,
            )
        else:
            print(f"  GUI session {api_key.env_var_name}: platform check not available", flush=True)
        print(f"  CLI can read provider env now: {'yes' if api_key.env_var_has_value else 'no'}", flush=True)
        if api_key.platform_name == "Darwin":
            print(
                f"  GUI apps can read provider env now: {'yes' if api_key.gui_env_var_has_value else 'no'}",
                flush=True,
            )
        else:
            print("  GUI apps can read provider env now: platform check not available", flush=True)
        print("", flush=True)
        print(f"Helper resolution if `api` validates now: {api_key.effective_source}", flush=True)
        if (api_key.keychain_has_value or api_key.file_has_value) and not api_key.env_var_has_value:
            print(
                "Note: a local helper key exists, but the provider env variable is not visible in the current shell.",
                flush=True,
            )
        if api_key.platform_name == "Darwin" and (api_key.keychain_has_value or api_key.file_has_value) and not api_key.gui_env_var_has_value:
            print(
                "Note: a local helper key exists, but GUI apps still cannot read XAI_API_KEY from launchctl.",
                flush=True,
            )

    if api_mode_active:
        if verbose:
            print("Codex CLI login status is skipped in API mode because provider auth is env-driven.", flush=True)
        return

    codex_login_status(codex_bin)


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


def handle_api_key_management(paths: Paths, args: argparse.Namespace) -> bool:
    if args.show_key:
        show_api_key_config(paths, show_full=False)
        return True
    if args.show_key_full:
        show_api_key_config(paths, show_full=True)
        return True
    if args.set_key is not None:
        set_api_key_config(paths, api_key=args.set_key, store=args.store)
        return True
    if args.prompt_key:
        set_api_key_config(paths, api_key=getpass.getpass("OpenAI API key: "), store=args.store)
        return True
    if args.clear_key:
        clear_api_key_config(paths, store=args.store)
        return True
    return False


def switch_chatgpt(paths: Paths, codex_bin: str) -> None:
    ensure_profile_dir(paths)
    if not paths.auth_file.exists() and not paths.chatgpt_auth_file.exists():
        raise CodexModeError("No Codex auth state found. Run `codex login` first.")

    save_current_snapshot(paths)
    require_file(paths.chatgpt_auth_file, "No saved ChatGPT session snapshot found. Use `chatgpt --relogin`.")

    shutil.copy2(paths.chatgpt_auth_file, paths.auth_file)
    remove_api_provider_config(paths.config_file)
    remove_openai_base_url(paths.config_file)

    print("Switched Codex to ChatGPT billing mode.", flush=True)
    print("If Codex App is open, fully quit and reopen it.", flush=True)
    codex_login_status(codex_bin)


def switch_or_relogin_chatgpt(paths: Paths, codex_bin: str, *, relogin: bool) -> None:
    if relogin:
        relogin_chatgpt(paths, codex_bin)
    else:
        switch_chatgpt(paths, codex_bin)


def switch_api(
    paths: Paths,
    codex_bin: str,
    *,
    base_url: str | None,
    refresh_auth: bool,
    prompt_for_key: bool,
) -> None:
    final_base_url = resolve_base_url(paths, base_url)
    if not final_base_url:
        raise CodexModeError("No API base URL configured. Pass `--base-url URL`.")

    api_key = resolve_api_key(paths, allow_prompt=prompt_for_key)
    if not api_key:
        raise CodexModeError(
            f"No API key is currently available from managed storage or the {API_PROVIDER_ENV_KEY} environment variable. "
            "Use `codex-mode api --prompt-key`, `codex-mode api --set-key ...`, "
            f"set {API_PROVIDER_ENV_KEY}, or rerun with `--prompt`."
        )

    ensure_profile_dir(paths)
    paths.config_file.parent.mkdir(parents=True, exist_ok=True)
    save_current_snapshot(paths)

    write_secret_text(paths.api_base_url_file, final_base_url)
    set_api_provider_config(paths.config_file, final_base_url)

    print("Switched Codex to API billing mode.", flush=True)
    print(f"Configured model_provider = {API_PROVIDER_ID}", flush=True)
    print(f"Configured provider base_url = {final_base_url}", flush=True)
    print(f"Configured provider env_key = {API_PROVIDER_ENV_KEY}", flush=True)
    print("If Codex App is open, fully quit and reopen it.", flush=True)
    if os.environ.get(API_PROVIDER_ENV_KEY, "").strip() == "":
        print(
            f"Note: the active shell does not currently expose {API_PROVIDER_ENV_KEY}. "
            "Make sure your app session can read that variable.",
            flush=True,
        )
    if platform.system() == "Darwin":
        print(
            f"Make sure {API_PROVIDER_ENV_KEY} is available to GUI apps in your login session.",
            flush=True,
        )


def switch_or_relogin_api(
    paths: Paths,
    codex_bin: str,
    *,
    base_url: str | None,
    relogin: bool,
    prompt_for_key: bool,
) -> None:
    switch_api(
        paths,
        codex_bin,
        base_url=base_url,
        refresh_auth=relogin,
        prompt_for_key=prompt_for_key,
    )


def relogin_chatgpt(paths: Paths, codex_bin: str) -> None:
    ensure_profile_dir(paths)
    remove_api_provider_config(paths.config_file)
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
          codex-mode status --verbose
          codex-mode chatgpt
          codex-mode chatgpt --relogin
          codex-mode api --base-url https://api.xairouter.com
          codex-mode api --relogin
          codex-mode api --relogin --prompt
          codex-mode api --show-key
          codex-mode api --prompt-key
          codex-mode api --clear-key
          codex-mode update
          codex-mode update --check
          codex-mode update --download
          codex-mode update --repo C:\\path\\to\\codex-mode

        API-key lookup order:
          macOS: Keychain -> managed file -> XAI_API_KEY -> interactive prompt
          Windows/Linux: managed file -> XAI_API_KEY -> interactive prompt

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
    chatgpt_parser = sub.add_parser(
        "chatgpt",
        help="Switch to the saved ChatGPT login snapshot",
        description=(
            "Restore the saved ChatGPT auth snapshot and remove the managed API provider config block. "
            "Use --relogin to run a fresh `codex login` and refresh the saved snapshot."
        ),
    )
    chatgpt_parser.add_argument(
        "--relogin",
        action="store_true",
        help="Run a fresh ChatGPT login and refresh the saved snapshot before switching",
    )

    api_parser = sub.add_parser(
        "api",
        help="Switch to API-key mode",
        description=(
            "Switch Codex into API-key mode by writing a managed provider block. "
            "Optionally set --base-url and use --relogin to force a fresh key validation. "
            "By default this command does not prompt for an API key."
        ),
    )
    api_parser.add_argument("--base-url")
    api_parser.add_argument(
        "--relogin",
        action="store_true",
        help="Force a fresh API-key validation before rewriting the managed provider block",
    )
    api_parser.add_argument("--prompt", action="store_true", help="Allow a secure prompt for the API key if no stored key is available")
    api_key_group = api_parser.add_mutually_exclusive_group()
    api_key_group.add_argument("--show-key", action="store_true", help="Show the current effective API key in masked form")
    api_key_group.add_argument("--show-key-full", action="store_true", help="Show the full effective API key")
    api_key_group.add_argument("--set-key", metavar="KEY", help="Save a helper-managed XAI_API_KEY value")
    api_key_group.add_argument("--prompt-key", action="store_true", help="Prompt securely for an XAI_API_KEY value and save it")
    api_key_group.add_argument("--clear-key", action="store_true", help="Clear the selected helper-managed XAI_API_KEY value")
    api_parser.add_argument(
        "--store",
        choices=["auto", "keychain", "file"],
        default="auto",
        help="Select where to save or clear the helper-managed XAI_API_KEY value",
    )

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
        description="Show the top-level help text or help for one specific subcommand, such as `codex-mode help api`.",
    )
    help_parser.add_argument("topic", nargs="?")

    parser._subcommand_parsers = {
        "status": status_parser,
        "chatgpt": chatgpt_parser,
        "api": api_parser,
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
        elif args.command == "chatgpt":
            switch_or_relogin_chatgpt(paths, codex_bin, relogin=args.relogin)
        elif args.command == "api":
            if handle_api_key_management(paths, args):
                return 0
            switch_or_relogin_api(
                paths,
                codex_bin,
                base_url=args.base_url,
                relogin=args.relogin,
                prompt_for_key=args.prompt,
            )
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
