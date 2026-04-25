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
DEFAULT_API_GROUP = "default"
DEFAULT_CHATGPT_GROUP = "default"
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


def read_program_version() -> str:
    version_file = pathlib.Path(__file__).resolve().with_name("VERSION")
    if not version_file.exists():
        return "unknown"
    try:
        return version_file.read_text(encoding="utf-8").strip() or "unknown"
    except Exception:
        return "unknown"


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
    api_groups_file: pathlib.Path
    chatgpt_groups_file: pathlib.Path


@dataclass
class ApiKeyInspection:
    group_name: str
    platform_name: str
    keychain_supported: bool
    keychain_service: str
    keychain_has_value: bool
    file_has_value: bool
    env_var_name: str
    fallback_env_var_names: list[str]
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
        api_auth_file=codex_home / "auth-profiles" / "api.auth.json",
        api_base_url_file=codex_home / "auth-profiles" / "api.base_url",
        api_key_file=codex_home / "auth-profiles" / "api.key",
        api_groups_file=codex_home / "auth-profiles" / "api.groups.json",
        chatgpt_groups_file=codex_home / "auth-profiles" / "chatgpt.groups.json",
    )


def normalize_group_name(name: str) -> str:
    normalized = (name or "").strip().lower()
    if not normalized:
        raise CodexModeError("API group name is empty.")
    if not re.match(r"^[a-z0-9._-]+$", normalized):
        raise CodexModeError(
            "API group names may only use lowercase letters, digits, dot, underscore, and hyphen."
        )
    return normalized


def default_api_groups_state() -> dict[str, object]:
    return {
        "version": 1,
        "default_group": DEFAULT_API_GROUP,
        "current_group": DEFAULT_API_GROUP,
        "groups": {
            DEFAULT_API_GROUP: {
                "env_var_name": API_PROVIDER_ENV_KEY,
            }
        },
    }


def load_api_groups_state(paths: Paths) -> dict[str, object]:
    state = default_api_groups_state()
    if not paths.api_groups_file.exists():
        return state

    try:
        loaded = json.loads(paths.api_groups_file.read_text(encoding="utf-8"))
    except Exception:
        return state

    groups = loaded.get("groups", {})
    normalized_groups: dict[str, dict[str, str]] = {}
    if isinstance(groups, dict):
        for raw_name, raw_config in groups.items():
            try:
                name = normalize_group_name(str(raw_name))
            except CodexModeError:
                continue
            env_var_name = API_PROVIDER_ENV_KEY
            if isinstance(raw_config, dict):
                candidate = str(raw_config.get("env_var_name", "") or "").strip()
                if candidate:
                    env_var_name = candidate
            normalized_groups[name] = {
                "env_var_name": env_var_name,
            }

    if DEFAULT_API_GROUP not in normalized_groups:
        normalized_groups[DEFAULT_API_GROUP] = {"env_var_name": API_PROVIDER_ENV_KEY}

    default_group = str(loaded.get("default_group", DEFAULT_API_GROUP) or DEFAULT_API_GROUP)
    current_group = str(loaded.get("current_group", default_group) or default_group)

    try:
        default_group = normalize_group_name(default_group)
    except CodexModeError:
        default_group = DEFAULT_API_GROUP
    if default_group not in normalized_groups:
        normalized_groups[default_group] = {"env_var_name": API_PROVIDER_ENV_KEY}

    try:
        current_group = normalize_group_name(current_group)
    except CodexModeError:
        current_group = default_group
    if current_group not in normalized_groups:
        normalized_groups[current_group] = {"env_var_name": API_PROVIDER_ENV_KEY}

    state["default_group"] = default_group
    state["current_group"] = current_group
    state["groups"] = normalized_groups
    return state


def save_api_groups_state(paths: Paths, state: dict[str, object]) -> None:
    paths.api_groups_file.parent.mkdir(parents=True, exist_ok=True)
    paths.api_groups_file.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def ensure_api_group_entry(state: dict[str, object], group_name: str) -> dict[str, str]:
    groups = state.setdefault("groups", {})
    assert isinstance(groups, dict)
    entry = groups.get(group_name)
    if not isinstance(entry, dict):
        entry = {"env_var_name": API_PROVIDER_ENV_KEY}
        groups[group_name] = entry
    env_var_name = str(entry.get("env_var_name", "") or "").strip() or API_PROVIDER_ENV_KEY
    entry["env_var_name"] = env_var_name
    return entry


def resolve_api_group_name(paths: Paths, explicit_group: str | None) -> str:
    if explicit_group:
        return normalize_group_name(explicit_group)
    state = load_api_groups_state(paths)
    return normalize_group_name(str(state.get("default_group", DEFAULT_API_GROUP) or DEFAULT_API_GROUP))


def api_group_env_var_name(paths: Paths, group_name: str) -> str:
    state = load_api_groups_state(paths)
    entry = ensure_api_group_entry(state, group_name)
    return entry["env_var_name"]


def api_group_env_var_candidates(paths: Paths, group_name: str) -> list[str]:
    candidates = [
        api_group_env_var_name(paths, group_name),
        API_PROVIDER_ENV_KEY,
        "OPENAI_API_KEY",
    ]
    unique: list[str] = []
    for candidate in candidates:
        normalized = candidate.strip()
        if normalized and normalized not in unique:
            unique.append(normalized)
    return unique


def keychain_service_for_group(group_name: str) -> str:
    if group_name == DEFAULT_API_GROUP:
        return KEYCHAIN_SERVICE
    return f"{KEYCHAIN_SERVICE}.{group_name}"


def api_group_auth_file(paths: Paths, group_name: str) -> pathlib.Path:
    if group_name == DEFAULT_API_GROUP:
        return paths.api_auth_file
    return paths.profile_dir / f"api.{group_name}.auth.json"


def api_group_base_url_file(paths: Paths, group_name: str) -> pathlib.Path:
    if group_name == DEFAULT_API_GROUP:
        return paths.api_base_url_file
    return paths.profile_dir / f"api.{group_name}.base_url"


def api_group_key_file(paths: Paths, group_name: str) -> pathlib.Path:
    if group_name == DEFAULT_API_GROUP:
        return paths.api_key_file
    return paths.profile_dir / f"api.{group_name}.key"


def default_chatgpt_groups_state() -> dict[str, object]:
    return {
        "version": 1,
        "default_group": DEFAULT_CHATGPT_GROUP,
        "current_group": DEFAULT_CHATGPT_GROUP,
        "groups": {
            DEFAULT_CHATGPT_GROUP: {},
        },
    }


def load_chatgpt_groups_state(paths: Paths) -> dict[str, object]:
    state = default_chatgpt_groups_state()
    if not paths.chatgpt_groups_file.exists():
        return state

    try:
        loaded = json.loads(paths.chatgpt_groups_file.read_text(encoding="utf-8"))
    except Exception:
        return state

    groups = loaded.get("groups", {})
    normalized_groups: dict[str, dict[str, str]] = {}
    if isinstance(groups, dict):
        for raw_name, raw_config in groups.items():
            try:
                name = normalize_group_name(str(raw_name))
            except CodexModeError:
                continue
            normalized_groups[name] = raw_config if isinstance(raw_config, dict) else {}

    if DEFAULT_CHATGPT_GROUP not in normalized_groups:
        normalized_groups[DEFAULT_CHATGPT_GROUP] = {}

    default_group = str(loaded.get("default_group", DEFAULT_CHATGPT_GROUP) or DEFAULT_CHATGPT_GROUP)
    current_group = str(loaded.get("current_group", default_group) or default_group)

    try:
        default_group = normalize_group_name(default_group)
    except CodexModeError:
        default_group = DEFAULT_CHATGPT_GROUP
    if default_group not in normalized_groups:
        normalized_groups[default_group] = {}

    try:
        current_group = normalize_group_name(current_group)
    except CodexModeError:
        current_group = default_group
    if current_group not in normalized_groups:
        normalized_groups[current_group] = {}

    state["default_group"] = default_group
    state["current_group"] = current_group
    state["groups"] = normalized_groups
    return state


def save_chatgpt_groups_state(paths: Paths, state: dict[str, object]) -> None:
    paths.chatgpt_groups_file.parent.mkdir(parents=True, exist_ok=True)
    paths.chatgpt_groups_file.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def ensure_chatgpt_group_entry(state: dict[str, object], group_name: str) -> dict[str, str]:
    groups = state.setdefault("groups", {})
    assert isinstance(groups, dict)
    entry = groups.get(group_name)
    if not isinstance(entry, dict):
        entry = {}
        groups[group_name] = entry
    return entry


def resolve_chatgpt_group_name(paths: Paths, explicit_group: str | None) -> str:
    if explicit_group:
        return normalize_group_name(explicit_group)
    state = load_chatgpt_groups_state(paths)
    return normalize_group_name(str(state.get("default_group", DEFAULT_CHATGPT_GROUP) or DEFAULT_CHATGPT_GROUP))


def chatgpt_group_auth_file(paths: Paths, group_name: str) -> pathlib.Path:
    if group_name == DEFAULT_CHATGPT_GROUP:
        return paths.chatgpt_auth_file
    return paths.profile_dir / f"chatgpt.{group_name}.auth.json"


def set_default_chatgpt_group(paths: Paths, group_name: str) -> None:
    state = load_chatgpt_groups_state(paths)
    ensure_chatgpt_group_entry(state, group_name)
    state["default_group"] = group_name
    if not str(state.get("current_group", "") or "").strip():
        state["current_group"] = group_name
    save_chatgpt_groups_state(paths, state)
    print(f"Default ChatGPT group set to: {group_name}", flush=True)


def set_current_chatgpt_group(paths: Paths, group_name: str) -> None:
    state = load_chatgpt_groups_state(paths)
    ensure_chatgpt_group_entry(state, group_name)
    state["current_group"] = group_name
    save_chatgpt_groups_state(paths, state)


def list_chatgpt_groups(paths: Paths) -> None:
    state = load_chatgpt_groups_state(paths)
    default_group = normalize_group_name(str(state.get("default_group", DEFAULT_CHATGPT_GROUP) or DEFAULT_CHATGPT_GROUP))
    current_group = normalize_group_name(str(state.get("current_group", default_group) or default_group))
    groups = state.get("groups", {})
    assert isinstance(groups, dict)

    print("ChatGPT groups:", flush=True)
    for group_name in sorted(groups):
        markers: list[str] = []
        if group_name == default_group:
            markers.append("default")
        if group_name == current_group:
            markers.append("current")
        marker_text = f" ({', '.join(markers)})" if markers else ""
        auth_present = chatgpt_group_auth_file(paths, group_name).exists()
        print(f"- {group_name}{marker_text}: auth_snapshot={'yes' if auth_present else 'no'}", flush=True)


def remove_chatgpt_group(paths: Paths, group_name: str) -> None:
    if group_name == DEFAULT_CHATGPT_GROUP:
        raise CodexModeError("The default ChatGPT group cannot be removed.")

    state = load_chatgpt_groups_state(paths)
    groups = state.get("groups", {})
    assert isinstance(groups, dict)
    groups.pop(group_name, None)
    if str(state.get("default_group", DEFAULT_CHATGPT_GROUP) or DEFAULT_CHATGPT_GROUP) == group_name:
        state["default_group"] = DEFAULT_CHATGPT_GROUP
    if str(state.get("current_group", DEFAULT_CHATGPT_GROUP) or DEFAULT_CHATGPT_GROUP) == group_name:
        state["current_group"] = DEFAULT_CHATGPT_GROUP
    save_chatgpt_groups_state(paths, state)

    auth_path = chatgpt_group_auth_file(paths, group_name)
    if auth_path.exists():
        auth_path.unlink()

    print(f"Removed ChatGPT group: {group_name}", flush=True)


def show_chatgpt_auth_file(paths: Paths, group_name: str) -> None:
    print(chatgpt_group_auth_file(paths, group_name), flush=True)


def import_chatgpt_auth_file(paths: Paths, group_name: str, source: str) -> None:
    source_path = pathlib.Path(source).expanduser().resolve()
    require_file(source_path, f"Auth file not found: {source_path}")
    try:
        data = json.loads(source_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CodexModeError(f"Could not parse auth file: {source_path}") from exc

    auth_mode = str(data.get("auth_mode", "") or "")
    if auth_mode != "chatgpt":
        raise CodexModeError(
            f"Imported auth file must have auth_mode='chatgpt', got '{auth_mode or 'unknown'}'."
        )

    target = chatgpt_group_auth_file(paths, group_name)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target)
    state = load_chatgpt_groups_state(paths)
    ensure_chatgpt_group_entry(state, group_name)
    save_chatgpt_groups_state(paths, state)
    print(f"Imported ChatGPT auth snapshot for group '{group_name}' from: {source_path}", flush=True)
    print(f"Managed auth snapshot: {target}", flush=True)


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


def read_provider_env_key(config_file: pathlib.Path, provider_id: str) -> str:
    text = read_config_text(config_file)
    pattern = (
        rf"(?ms)^\[model_providers\.{re.escape(provider_id)}\]\s*$"
        rf"(.*?)(?=^\[|\Z)"
    )
    match = re.search(pattern, text)
    if not match:
        return ""
    section_body = match.group(1)
    value_match = re.search(r'(?m)^\s*env_key\s*=\s*"([^"]+)"\s*$', section_body)
    return value_match.group(1) if value_match else ""


def api_provider_config_is_active(config_file: pathlib.Path) -> bool:
    text = read_config_text(config_file)
    return (
        API_CONFIG_START_COMMENT in text
        or read_model_provider(config_file) == API_PROVIDER_ID
        or bool(read_provider_base_url(config_file, API_PROVIDER_ID))
    )


def render_api_provider_block(base_url: str, env_var_name: str, newline: str) -> list[str]:
    return [
        f"{API_CONFIG_START_COMMENT}{newline}",
        f'model_provider = "{API_PROVIDER_ID}"{newline}',
        newline,
        f"[model_providers.{API_PROVIDER_ID}]{newline}",
        f'name = "{API_PROVIDER_NAME}"{newline}',
        f'base_url = "{base_url}"{newline}',
        f'wire_api = "{API_PROVIDER_WIRE_API}"{newline}',
        f"requires_openai_auth = {API_PROVIDER_REQUIRES_OPENAI_AUTH}{newline}",
        f'env_key = "{env_var_name}"{newline}',
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


def set_api_provider_config(config_file: pathlib.Path, base_url: str, env_var_name: str) -> None:
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

    new_lines.extend(render_api_provider_block(base_url, env_var_name, newline))
    if after:
        new_lines.append(newline)
    new_lines.extend(after)

    write_config_text(config_file, "".join(collapse_consecutive_blank_lines(new_lines)))


def save_current_snapshot(paths: Paths) -> None:
    current_mode = read_auth_mode(paths.auth_file)
    if current_mode == "chatgpt":
        state = load_chatgpt_groups_state(paths)
        current_group = normalize_group_name(
            str(state.get("current_group", state.get("default_group", DEFAULT_CHATGPT_GROUP)) or DEFAULT_CHATGPT_GROUP)
        )
        shutil.copy2(paths.auth_file, chatgpt_group_auth_file(paths, current_group))
    elif current_mode == "apikey":
        state = load_api_groups_state(paths)
        current_group = normalize_group_name(
            str(state.get("current_group", state.get("default_group", DEFAULT_API_GROUP)) or DEFAULT_API_GROUP)
        )
        shutil.copy2(paths.auth_file, api_group_auth_file(paths, current_group))


def require_file(path: pathlib.Path, message: str) -> None:
    if not path.exists():
        raise CodexModeError(message)


def read_file_bytes(path: pathlib.Path) -> bytes | None:
    if not path.exists():
        return None
    return path.read_bytes()


def restore_file_bytes(path: pathlib.Path, data: bytes | None) -> None:
    if data is None:
        if path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def run_codex(codex_bin: str, args: list[str], *, input_text: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [codex_bin, *args],
        input=input_text,
        text=True,
        check=check,
    )


def codex_login_status(codex_bin: str) -> None:
    run_codex(codex_bin, ["login", "status"])


def read_mac_keychain_key(service_name: str | None = None) -> str:
    if platform.system() != "Darwin":
        return ""
    if not shutil.which("security"):
        return ""
    service = service_name or KEYCHAIN_SERVICE
    proc = subprocess.run(
        ["security", "find-generic-password", "-a", getpass.getuser(), "-s", service, "-w"],
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


def write_mac_keychain_key(value: str, service_name: str | None = None) -> None:
    if platform.system() != "Darwin":
        raise CodexModeError("macOS Keychain is only available on macOS.")
    if not shutil.which("security"):
        raise CodexModeError("Could not find the macOS `security` tool.")
    service = service_name or KEYCHAIN_SERVICE
    proc = subprocess.run(
        ["security", "add-generic-password", "-U", "-a", getpass.getuser(), "-s", service, "-w", value],
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise CodexModeError(proc.stderr.strip() or "Failed to write the API key to macOS Keychain.")


def remove_mac_keychain_key(service_name: str | None = None) -> None:
    if platform.system() != "Darwin":
        raise CodexModeError("macOS Keychain is only available on macOS.")
    if not shutil.which("security"):
        raise CodexModeError("Could not find the macOS `security` tool.")
    service = service_name or KEYCHAIN_SERVICE
    proc = subprocess.run(
        ["security", "delete-generic-password", "-a", getpass.getuser(), "-s", service],
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0 and "could not be found" not in proc.stderr.lower():
        raise CodexModeError(proc.stderr.strip() or "Failed to remove the API key from macOS Keychain.")


def read_managed_api_key(paths: Paths, group_name: str) -> str:
    return read_secret_text(api_group_key_file(paths, group_name))


def mask_secret(value: str) -> str:
    if not value:
        return "not set"
    if len(value) <= 8:
        if len(value) <= 2:
            return "*" * len(value)
        return value[:1] + "*" * (len(value) - 2) + value[-1:]
    return value[:4] + "*" * (len(value) - 8) + value[-4:]


def inspect_api_key_sources(paths: Paths, group_name: str) -> ApiKeyInspection:
    env_var_candidates = api_group_env_var_candidates(paths, group_name)
    env_var_name = env_var_candidates[0]
    env_value = ""
    env_source = env_var_name
    for candidate in env_var_candidates:
        candidate_value = os.environ.get(candidate, "").strip()
        if candidate_value:
            env_source = candidate
            env_value = candidate_value
            break
    gui_env_value = read_launchctl_env(env_var_name)
    keychain_service = keychain_service_for_group(group_name)
    keychain_value = read_mac_keychain_key(keychain_service)
    file_value = read_managed_api_key(paths, group_name)
    platform_name = current_platform_name()
    keychain_supported = platform_name == "Darwin" and shutil.which("security") is not None

    if keychain_value:
        effective_source = "macOS Keychain"
        effective_value = keychain_value
    elif file_value:
        effective_source = "managed file"
        effective_value = file_value
    elif env_value:
        effective_source = env_source
        effective_value = env_value
    else:
        effective_source = "interactive prompt"
        effective_value = ""

    return ApiKeyInspection(
        group_name=group_name,
        platform_name=platform_name,
        keychain_supported=keychain_supported,
        keychain_service=keychain_service,
        keychain_has_value=bool(keychain_value),
        file_has_value=bool(file_value),
        env_var_name=env_var_name,
        fallback_env_var_names=env_var_candidates[1:],
        env_var_has_value=bool(env_value),
        gui_env_var_has_value=bool(gui_env_value),
        effective_source=effective_source,
        effective_value=effective_value,
    )


def resolve_api_key(paths: Paths, group_name: str, *, allow_prompt: bool) -> str:
    key = read_mac_keychain_key(keychain_service_for_group(group_name))
    if key:
        return key

    key = read_managed_api_key(paths, group_name)
    if key:
        return key

    for env_var_name in api_group_env_var_candidates(paths, group_name):
        key = os.environ.get(env_var_name, "").strip()
        if key:
            return key

    if allow_prompt:
        return getpass.getpass("OpenAI API key: ").strip()

    return ""


def resolve_base_url(paths: Paths, arg_base_url: str | None, group_name: str) -> str:
    if arg_base_url:
        return arg_base_url
    base_url_file = api_group_base_url_file(paths, group_name)
    if base_url_file.exists():
        return base_url_file.read_text().strip()
    provider_base_url = read_provider_base_url(paths.config_file, API_PROVIDER_ID).strip()
    if provider_base_url:
        return provider_base_url
    return read_openai_base_url(paths.config_file).strip()


def print_status(paths: Paths, codex_bin: str, *, verbose: bool) -> None:
    groups_state = load_api_groups_state(paths)
    default_group = normalize_group_name(str(groups_state.get("default_group", DEFAULT_API_GROUP) or DEFAULT_API_GROUP))
    current_group = normalize_group_name(str(groups_state.get("current_group", default_group) or default_group))
    chatgpt_groups_state = load_chatgpt_groups_state(paths)
    default_chatgpt_group = normalize_group_name(
        str(chatgpt_groups_state.get("default_group", DEFAULT_CHATGPT_GROUP) or DEFAULT_CHATGPT_GROUP)
    )
    current_chatgpt_group = normalize_group_name(
        str(chatgpt_groups_state.get("current_group", default_chatgpt_group) or default_chatgpt_group)
    )
    auth_mode = read_auth_mode(paths.auth_file)
    api_mode_active = api_provider_config_is_active(paths.config_file)
    config_base_url = read_openai_base_url(paths.config_file)
    provider_base_url = read_provider_base_url(paths.config_file, API_PROVIDER_ID)
    provider_env_key = read_provider_env_key(paths.config_file, API_PROVIDER_ID)
    saved_api_base_url_file = api_group_base_url_file(paths, current_group)
    saved_api_base_url = saved_api_base_url_file.read_text().strip() if saved_api_base_url_file.exists() else ""
    effective_api_base_url = resolve_base_url(paths, None, current_group)
    api_key = inspect_api_key_sources(paths, current_group)

    if api_mode_active:
        print(f"Current mode: API key (provider, group: {current_group})", flush=True)
    elif auth_mode == "apikey":
        print(f"Current mode: legacy API auth (group: {current_group})", flush=True)
    elif auth_mode == "chatgpt":
        print(f"Current mode: ChatGPT (group: {current_chatgpt_group})", flush=True)
    elif auth_mode:
        print(f"Current mode: {auth_mode}", flush=True)
    else:
        print("Current mode: unknown", flush=True)

    if api_mode_active:
        print(f"Effective API base URL: {effective_api_base_url or 'not set'}", flush=True)

    if verbose:
        print(f"Codex home: {paths.codex_home}", flush=True)
        print(f"Auth file: {'present' if paths.auth_file.exists() else 'missing'}", flush=True)
        print(f"Default ChatGPT group: {default_chatgpt_group}", flush=True)
        print(f"Current ChatGPT group: {current_chatgpt_group}", flush=True)
        print(
            f"Saved ChatGPT snapshot: {'present' if chatgpt_group_auth_file(paths, current_chatgpt_group).exists() else 'missing'}",
            flush=True,
        )
        print(f"Default API group: {default_group}", flush=True)
        print(f"Current API group: {current_group}", flush=True)
        print(
            f"Saved legacy API snapshot: {'present' if api_group_auth_file(paths, current_group).exists() else 'missing'}",
            flush=True,
        )
        print("Default API switch strategy: legacy auth snapshot", flush=True)
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
        print(f"  Expected provider env key: {provider_env_key or api_key.env_var_name}", flush=True)
        print(f"  requires_openai_auth: {API_PROVIDER_REQUIRES_OPENAI_AUTH}", flush=True)
        print("Local helper availability:", flush=True)
        if api_key.platform_name == "Darwin":
            print(
                f"  macOS Keychain ({api_key.keychain_service}): {'set' if api_key.keychain_has_value else 'not set'}",
                flush=True,
            )
        print(
            f"  Managed file ({api_group_key_file(paths, current_group)}): {'set' if api_key.file_has_value else 'not set'}",
            flush=True,
        )
        print("Environment visibility:", flush=True)
        print(
            f"  Current shell {api_key.env_var_name}: {'set' if api_key.env_var_has_value else 'not set'}",
            flush=True,
        )
        for fallback_env_var in api_key.fallback_env_var_names:
            print(
                f"  Fallback shell {fallback_env_var}: {'set' if bool(os.environ.get(fallback_env_var, '').strip()) else 'not set'}",
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
                f"Note: a local helper key exists, but GUI apps still cannot read {api_key.env_var_name} from launchctl.",
                flush=True,
            )

        print("", flush=True)
        print("Configured ChatGPT groups:", flush=True)
        chatgpt_groups = chatgpt_groups_state.get("groups", {})
        assert isinstance(chatgpt_groups, dict)
        for group_name in sorted(chatgpt_groups):
            markers: list[str] = []
            if group_name == default_chatgpt_group:
                markers.append("default")
            if group_name == current_chatgpt_group:
                markers.append("current")
            marker_text = f" ({', '.join(markers)})" if markers else ""
            auth_present = chatgpt_group_auth_file(paths, group_name).exists()
            print(f"  {group_name}{marker_text}: auth_snapshot={'yes' if auth_present else 'no'}", flush=True)

        print("", flush=True)
        print("Configured API groups:", flush=True)
        groups = groups_state.get("groups", {})
        assert isinstance(groups, dict)
        for group_name in sorted(groups):
            markers: list[str] = []
            if group_name == default_group:
                markers.append("default")
            if group_name == current_group:
                markers.append("current")
            marker_text = f" ({', '.join(markers)})" if markers else ""
            group_base_url = read_secret_text(api_group_base_url_file(paths, group_name)) or "not set"
            group_env_var = api_group_env_var_name(paths, group_name)
            print(f"  {group_name}{marker_text}: base_url={group_base_url}, env_var={group_env_var}", flush=True)

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


def show_api_key_config(paths: Paths, group_name: str, *, show_full: bool) -> None:
    inspection = inspect_api_key_sources(paths, group_name)
    print(f"Group: {group_name}", flush=True)
    print(f"Effective source: {inspection.effective_source}", flush=True)
    if inspection.effective_value:
        value = inspection.effective_value if show_full else mask_secret(inspection.effective_value)
        print(f"Effective API key: {value}", flush=True)
    else:
        print("Effective API key: not set", flush=True)


def set_api_key_config(paths: Paths, group_name: str, *, api_key: str, store: str) -> None:
    normalized = api_key.strip()
    if not normalized:
        raise CodexModeError("API key is empty.")

    resolved_store = resolve_api_key_store(store)
    if resolved_store == "keychain":
        write_mac_keychain_key(normalized, keychain_service_for_group(group_name))
        print(f"Saved the API key for group '{group_name}' to macOS Keychain.", flush=True)
    elif resolved_store == "file":
        key_file = api_group_key_file(paths, group_name)
        write_secret_text(key_file, normalized)
        print(f"Saved the API key for group '{group_name}' to the managed file: {key_file}", flush=True)
    else:
        raise CodexModeError(f"Unsupported API-key store: {resolved_store}")

    print(f"Stored API key: {mask_secret(normalized)}", flush=True)


def clear_api_key_config(paths: Paths, group_name: str, *, store: str) -> None:
    resolved_store = resolve_api_key_store(store)
    if resolved_store == "keychain":
        remove_mac_keychain_key(keychain_service_for_group(group_name))
        print(f"Cleared the API key for group '{group_name}' from macOS Keychain.", flush=True)
    elif resolved_store == "file":
        key_file = api_group_key_file(paths, group_name)
        remove_secret_file(key_file)
        print(f"Cleared the managed API key file for group '{group_name}': {key_file}", flush=True)
    else:
        raise CodexModeError(f"Unsupported API-key store: {resolved_store}")
    print("Environment variables are not modified by codex-mode.", flush=True)


def list_api_groups(paths: Paths) -> None:
    state = load_api_groups_state(paths)
    default_group = normalize_group_name(str(state.get("default_group", DEFAULT_API_GROUP) or DEFAULT_API_GROUP))
    current_group = normalize_group_name(str(state.get("current_group", default_group) or default_group))
    groups = state.get("groups", {})
    assert isinstance(groups, dict)

    print("API groups:", flush=True)
    for group_name in sorted(groups):
        markers: list[str] = []
        if group_name == default_group:
            markers.append("default")
        if group_name == current_group:
            markers.append("current")
        marker_text = f" ({', '.join(markers)})" if markers else ""
        base_url = read_secret_text(api_group_base_url_file(paths, group_name)) or "not set"
        env_var_name = api_group_env_var_name(paths, group_name)
        auth_present = api_group_auth_file(paths, group_name).exists()
        print(
            f"- {group_name}{marker_text}: base_url={base_url}, env_var={env_var_name}, auth_snapshot={'yes' if auth_present else 'no'}",
            flush=True,
        )


def save_api_group_config(
    paths: Paths,
    group_name: str,
    *,
    base_url: str | None,
    env_var_name: str | None,
) -> None:
    state = load_api_groups_state(paths)
    groups = state.get("groups", {})
    assert isinstance(groups, dict)
    existed = group_name in groups
    entry = ensure_api_group_entry(state, group_name)
    changed = not existed
    if env_var_name is not None:
        normalized_env_var_name = env_var_name.strip()
        if not normalized_env_var_name:
            raise CodexModeError("Environment variable name is empty.")
        entry["env_var_name"] = normalized_env_var_name
        changed = True
    if changed:
        save_api_groups_state(paths, state)
    if base_url is not None:
        write_secret_text(api_group_base_url_file(paths, group_name), base_url)
    print(f"Saved API group '{group_name}'.", flush=True)
    print(
        f"base_url: {read_secret_text(api_group_base_url_file(paths, group_name)) or 'not set'}",
        flush=True,
    )
    print(f"env_var: {api_group_env_var_name(paths, group_name)}", flush=True)


def set_default_api_group(paths: Paths, group_name: str) -> None:
    state = load_api_groups_state(paths)
    ensure_api_group_entry(state, group_name)
    state["default_group"] = group_name
    if not str(state.get("current_group", "") or "").strip():
        state["current_group"] = group_name
    save_api_groups_state(paths, state)
    print(f"Default API group set to: {group_name}", flush=True)


def set_current_api_group(paths: Paths, group_name: str) -> None:
    state = load_api_groups_state(paths)
    ensure_api_group_entry(state, group_name)
    state["current_group"] = group_name
    save_api_groups_state(paths, state)


def remove_api_group(paths: Paths, group_name: str) -> None:
    if group_name == DEFAULT_API_GROUP:
        raise CodexModeError("The default API group cannot be removed.")

    state = load_api_groups_state(paths)
    groups = state.get("groups", {})
    assert isinstance(groups, dict)
    groups.pop(group_name, None)
    if str(state.get("default_group", DEFAULT_API_GROUP) or DEFAULT_API_GROUP) == group_name:
        state["default_group"] = DEFAULT_API_GROUP
    if str(state.get("current_group", DEFAULT_API_GROUP) or DEFAULT_API_GROUP) == group_name:
        state["current_group"] = DEFAULT_API_GROUP
    save_api_groups_state(paths, state)

    for path in (
        api_group_auth_file(paths, group_name),
        api_group_base_url_file(paths, group_name),
        api_group_key_file(paths, group_name),
    ):
        if path.exists():
            path.unlink()

    print(f"Removed API group: {group_name}", flush=True)


def show_api_auth_file(paths: Paths, group_name: str) -> None:
    print(api_group_auth_file(paths, group_name), flush=True)


def import_api_auth_file(paths: Paths, group_name: str, source: str) -> None:
    source_path = pathlib.Path(source).expanduser().resolve()
    require_file(source_path, f"Auth file not found: {source_path}")
    try:
        data = json.loads(source_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CodexModeError(f"Could not parse auth file: {source_path}") from exc

    auth_mode = str(data.get("auth_mode", "") or "")
    if auth_mode != "apikey":
        raise CodexModeError(
            f"Imported auth file must have auth_mode='apikey', got '{auth_mode or 'unknown'}'."
        )

    target = api_group_auth_file(paths, group_name)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target)
    print(f"Imported API auth snapshot for group '{group_name}' from: {source_path}", flush=True)
    print(f"Managed auth snapshot: {target}", flush=True)


def handle_api_key_management(paths: Paths, args: argparse.Namespace, group_name: str) -> bool:
    if args.show_key:
        show_api_key_config(paths, group_name, show_full=False)
        return True
    if args.show_key_full:
        show_api_key_config(paths, group_name, show_full=True)
        return True
    if args.set_key is not None:
        set_api_key_config(paths, group_name, api_key=args.set_key, store=args.store)
        return True
    if args.prompt_key:
        set_api_key_config(paths, group_name, api_key=getpass.getpass("OpenAI API key: "), store=args.store)
        return True
    if args.clear_key:
        clear_api_key_config(paths, group_name, store=args.store)
        return True
    return False


def handle_api_group_management(paths: Paths, args: argparse.Namespace, group_name: str) -> bool:
    did_work = False

    if args.list_groups:
        list_api_groups(paths)
        return True

    if args.remove_group is not None:
        remove_api_group(paths, normalize_group_name(args.remove_group))
        did_work = True

    if args.set_default_group is not None:
        set_default_api_group(paths, normalize_group_name(args.set_default_group))
        did_work = True

    if args.save_group:
        save_api_group_config(
            paths,
            group_name,
            base_url=args.base_url,
            env_var_name=args.env_var,
        )
        did_work = True

    if args.import_auth is not None:
        import_api_auth_file(paths, group_name, args.import_auth)
        did_work = True

    if args.show_auth_file:
        show_api_auth_file(paths, group_name)
        did_work = True

    if handle_api_key_management(paths, args, group_name):
        did_work = True

    return did_work


def handle_chatgpt_group_management(paths: Paths, args: argparse.Namespace, group_name: str) -> bool:
    did_work = False

    if args.list_groups:
        list_chatgpt_groups(paths)
        return True

    if args.remove_group is not None:
        remove_chatgpt_group(paths, normalize_group_name(args.remove_group))
        did_work = True

    if args.set_default_group is not None:
        set_default_chatgpt_group(paths, normalize_group_name(args.set_default_group))
        did_work = True

    if args.import_auth is not None:
        import_chatgpt_auth_file(paths, group_name, args.import_auth)
        did_work = True

    if args.show_auth_file:
        show_chatgpt_auth_file(paths, group_name)
        did_work = True

    return did_work


def switch_chatgpt(paths: Paths, codex_bin: str, *, group_name: str) -> None:
    ensure_profile_dir(paths)
    group_auth_file = chatgpt_group_auth_file(paths, group_name)
    if not paths.auth_file.exists() and not group_auth_file.exists():
        raise CodexModeError("No Codex auth state found. Run `codex login` first.")

    save_current_snapshot(paths)
    require_file(
        group_auth_file,
        f"No saved ChatGPT session snapshot found for group '{group_name}'. Use `chatgpt --group {group_name} --relogin`.",
    )

    shutil.copy2(group_auth_file, paths.auth_file)
    set_current_chatgpt_group(paths, group_name)
    remove_api_provider_config(paths.config_file)
    remove_openai_base_url(paths.config_file)

    print(f"Switched Codex to ChatGPT billing mode: {group_name}", flush=True)
    print("If Codex App is open, fully quit and reopen it.", flush=True)
    codex_login_status(codex_bin)


def switch_or_relogin_chatgpt(
    paths: Paths,
    codex_bin: str,
    *,
    group_name: str,
    relogin: bool,
    device_auth: bool,
) -> None:
    if device_auth and not relogin:
        raise CodexModeError("`--device-auth` only applies when `--relogin` is also passed.")
    if relogin:
        relogin_chatgpt(paths, codex_bin, group_name=group_name, device_auth=device_auth)
    else:
        switch_chatgpt(paths, codex_bin, group_name=group_name)


def switch_api(
    paths: Paths,
    codex_bin: str,
    *,
    group_name: str,
    base_url: str | None,
    refresh_auth: bool,
    prompt_for_key: bool,
    provider_mode: bool,
) -> None:
    if provider_mode:
        switch_api_provider(
            paths,
            codex_bin,
            group_name=group_name,
            base_url=base_url,
            refresh_auth=refresh_auth,
            prompt_for_key=prompt_for_key,
        )
    else:
        switch_api_legacy(
            paths,
            codex_bin,
            group_name=group_name,
            base_url=base_url,
            refresh_auth=refresh_auth,
            prompt_for_key=prompt_for_key,
        )


def switch_api_provider(
    paths: Paths,
    codex_bin: str,
    *,
    group_name: str,
    base_url: str | None,
    refresh_auth: bool,
    prompt_for_key: bool,
) -> None:
    del codex_bin
    del refresh_auth

    final_base_url = resolve_base_url(paths, base_url, group_name)
    if not final_base_url:
        raise CodexModeError("No API base URL configured. Pass `--base-url URL`.")

    api_key = resolve_api_key(paths, group_name, allow_prompt=prompt_for_key)
    if not api_key:
        preferred_env_var = api_group_env_var_name(paths, group_name)
        raise CodexModeError(
            f"No API key is currently available for group '{group_name}' from managed storage or the {preferred_env_var} environment variable. "
            "Use `codex-mode api --prompt-key`, `codex-mode api --set-key ...`, "
            f"set {preferred_env_var}, or rerun with `--prompt`."
        )

    ensure_profile_dir(paths)
    paths.config_file.parent.mkdir(parents=True, exist_ok=True)
    save_current_snapshot(paths)
    set_current_api_group(paths, group_name)

    write_secret_text(api_group_base_url_file(paths, group_name), final_base_url)
    set_api_provider_config(paths.config_file, final_base_url, api_group_env_var_name(paths, group_name))

    print(f"Switched Codex to API billing mode: {group_name}", flush=True)
    print("API scheme: provider config", flush=True)
    print(f"Configured model_provider = {API_PROVIDER_ID}", flush=True)
    print(f"Configured provider base_url = {final_base_url}", flush=True)
    print(f"Configured provider env_key = {api_group_env_var_name(paths, group_name)}", flush=True)
    print("If Codex App is open, fully quit and reopen it.", flush=True)
    preferred_env_var = api_group_env_var_name(paths, group_name)
    if os.environ.get(preferred_env_var, "").strip() == "":
        print(
            f"Note: the active shell does not currently expose {preferred_env_var}. "
            "Make sure your app session can read that variable.",
            flush=True,
        )
    if platform.system() == "Darwin":
        print(
            f"Make sure {preferred_env_var} is available to GUI apps in your login session.",
            flush=True,
        )


def switch_api_legacy(
    paths: Paths,
    codex_bin: str,
    *,
    group_name: str,
    base_url: str | None,
    refresh_auth: bool,
    prompt_for_key: bool,
) -> None:
    final_base_url = resolve_base_url(paths, base_url, group_name)
    if not final_base_url:
        raise CodexModeError("No API base URL configured. Pass `--base-url URL`.")

    ensure_profile_dir(paths)
    paths.config_file.parent.mkdir(parents=True, exist_ok=True)
    group_auth_file = api_group_auth_file(paths, group_name)
    group_base_url_file = api_group_base_url_file(paths, group_name)

    if not refresh_auth and group_auth_file.exists():
        save_current_snapshot(paths)
        write_secret_text(group_base_url_file, final_base_url)
        remove_api_provider_config(paths.config_file)
        set_openai_base_url(paths.config_file, final_base_url)
        shutil.copy2(group_auth_file, paths.auth_file)
        set_current_api_group(paths, group_name)
        print(f"Switched Codex to API billing mode: {group_name}", flush=True)
        print("API scheme: legacy auth snapshot", flush=True)
        print(f"Configured openai_base_url = {final_base_url}", flush=True)
        print("If Codex App is open, fully quit and reopen it.", flush=True)
        codex_login_status(codex_bin)
        return

    api_key = resolve_api_key(paths, group_name, allow_prompt=prompt_for_key)
    if not api_key:
        preferred_env_var = api_group_env_var_name(paths, group_name)
        raise CodexModeError(
            f"No API key is currently available for group '{group_name}' from managed storage or the {preferred_env_var} environment variable. "
            "Use `codex-mode api --prompt-key`, `codex-mode api --set-key ...`, "
            f"set {preferred_env_var}, or rerun with `--prompt`."
        )

    auth_backup = read_file_bytes(paths.auth_file)
    config_backup = read_file_bytes(paths.config_file)
    saved_base_url_backup = read_file_bytes(group_base_url_file)
    groups_backup = read_file_bytes(paths.api_groups_file)

    try:
        save_current_snapshot(paths)
        write_secret_text(group_base_url_file, final_base_url)
        remove_api_provider_config(paths.config_file)
        set_openai_base_url(paths.config_file, final_base_url)
        run_codex(codex_bin, ["login", "--with-api-key"], input_text=f"{api_key}\n")
        auth_mode = read_auth_mode(paths.auth_file)
        if auth_mode != "apikey":
            raise CodexModeError(
                f"API login completed, but the saved auth mode is '{auth_mode or 'unknown'}', not 'apikey'."
            )
        shutil.copy2(paths.auth_file, group_auth_file)
        set_current_api_group(paths, group_name)
    except Exception:
        restore_file_bytes(paths.auth_file, auth_backup)
        restore_file_bytes(paths.config_file, config_backup)
        restore_file_bytes(group_base_url_file, saved_base_url_backup)
        restore_file_bytes(paths.api_groups_file, groups_backup)
        raise

    print(f"Switched Codex to API billing mode: {group_name}", flush=True)
    print("API scheme: legacy auth snapshot", flush=True)
    print(f"Configured openai_base_url = {final_base_url}", flush=True)
    print("If Codex App is open, fully quit and reopen it.", flush=True)
    codex_login_status(codex_bin)


def switch_or_relogin_api(
    paths: Paths,
    codex_bin: str,
    *,
    group_name: str,
    base_url: str | None,
    relogin: bool,
    prompt_for_key: bool,
    provider_mode: bool,
) -> None:
    switch_api(
        paths,
        codex_bin,
        group_name=group_name,
        base_url=base_url,
        refresh_auth=relogin,
        prompt_for_key=prompt_for_key,
        provider_mode=provider_mode,
    )


def relogin_chatgpt(paths: Paths, codex_bin: str, *, group_name: str, device_auth: bool) -> None:
    ensure_profile_dir(paths)
    remove_api_provider_config(paths.config_file)
    remove_openai_base_url(paths.config_file)
    login_args = ["login"]
    if device_auth:
        login_args.append("--device-auth")
    run_codex(codex_bin, login_args)

    auth_mode = read_auth_mode(paths.auth_file)
    if auth_mode != "chatgpt":
        raise CodexModeError(
            f"Login completed, but the saved auth mode is '{auth_mode or 'unknown'}', not 'chatgpt'."
        )

    target_file = chatgpt_group_auth_file(paths, group_name)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(paths.auth_file, target_file)
    set_current_chatgpt_group(paths, group_name)
    print(f"Refreshed ChatGPT login snapshot: {group_name}", flush=True)
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
    program_version = read_program_version()
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
          codex-mode chatgpt --group work
          codex-mode chatgpt --relogin
          codex-mode chatgpt --group work --show-auth-file
          codex-mode chatgpt --group work --import-auth ./auth.json
          codex-mode chatgpt --list-groups
          codex-mode chatgpt --set-default-group work
          codex-mode api --base-url https://api.xairouter.com
          codex-mode api --group work
          codex-mode api --relogin
          codex-mode api --group work --base-url https://api.work.example --save-group
          codex-mode api --list-groups
          codex-mode api --set-default-group work
          codex-mode api --group work --show-auth-file
          codex-mode api --group work --import-auth ./auth.json
          codex-mode api --provider-mode --base-url https://api.xairouter.com
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

        API switching strategy:
          1. `codex-mode chatgpt` defaults to the default ChatGPT group
          2. `codex-mode chatgpt --group NAME` switches to one specific saved ChatGPT group
          3. `codex-mode api` defaults to the default API group and the legacy auth.json snapshot flow
          4. `codex-mode api --group NAME` switches to one specific saved API group
          5. `codex-mode api --provider-mode` uses the optional env-driven `model_provider = "xai"` config
          6. `codex-mode chatgpt` restores the saved ChatGPT snapshot and removes API-only config

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
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {program_version}",
        help="Show the installed codex-mode version and exit",
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
            "ChatGPT groups let you keep multiple ChatGPT auth.json snapshots side by side. "
            "Use --relogin to run a fresh `codex login` and refresh the selected snapshot."
        ),
    )
    chatgpt_parser.add_argument("--group", help="Operate on or switch to one saved ChatGPT group")
    chatgpt_parser.add_argument(
        "--relogin",
        action="store_true",
        help="Run a fresh ChatGPT login and refresh the saved snapshot before switching",
    )
    chatgpt_parser.add_argument(
        "--device-auth",
        action="store_true",
        help="Use `codex login --device-auth` when refreshing a ChatGPT login snapshot",
    )
    chatgpt_parser.add_argument("--list-groups", action="store_true", help="List saved ChatGPT groups")
    chatgpt_parser.add_argument("--set-default-group", metavar="GROUP", help="Set the default ChatGPT group")
    chatgpt_parser.add_argument("--remove-group", metavar="GROUP", help="Remove one saved ChatGPT group and its managed files")
    chatgpt_parser.add_argument("--show-auth-file", action="store_true", help="Print the managed auth snapshot path for the selected ChatGPT group")
    chatgpt_parser.add_argument("--import-auth", metavar="PATH", help="Import a user-managed auth.json file into the selected ChatGPT group")

    api_parser = sub.add_parser(
        "api",
        help="Switch to API-key mode",
        description=(
            "Switch Codex into API-key mode. By default this uses the legacy auth.json snapshot flow "
            "and keeps the shared chat-history behavior. API groups let you save multiple base URLs, "
            "API keys, and auth snapshots. Pass --provider-mode to use the optional env-driven provider "
            "block instead. By default this command does not prompt for an API key."
        ),
    )
    api_parser.add_argument("--group", help="Operate on or switch to one saved API group")
    api_parser.add_argument("--base-url")
    api_parser.add_argument("--env-var", help="Preferred environment variable name for this API group")
    api_parser.add_argument(
        "--provider-mode",
        action="store_true",
        help="Use the optional env-driven provider config instead of the default legacy auth snapshot flow",
    )
    api_parser.add_argument(
        "--relogin",
        action="store_true",
        help="Force a fresh API-key login/validation instead of reusing the saved API snapshot",
    )
    api_parser.add_argument("--prompt", action="store_true", help="Allow a secure prompt for the API key if no stored key is available")
    api_key_group = api_parser.add_mutually_exclusive_group()
    api_key_group.add_argument("--show-key", action="store_true", help="Show the current effective API key in masked form")
    api_key_group.add_argument("--show-key-full", action="store_true", help="Show the full effective API key")
    api_key_group.add_argument("--set-key", metavar="KEY", help="Save a helper-managed XAI_API_KEY value")
    api_key_group.add_argument("--prompt-key", action="store_true", help="Prompt securely for an XAI_API_KEY value and save it")
    api_key_group.add_argument("--clear-key", action="store_true", help="Clear the selected helper-managed XAI_API_KEY value")
    api_parser.add_argument("--list-groups", action="store_true", help="List saved API groups")
    api_parser.add_argument("--save-group", action="store_true", help="Save group metadata such as --base-url and --env-var without switching")
    api_parser.add_argument("--set-default-group", metavar="GROUP", help="Set the default API group used by `codex-mode api`")
    api_parser.add_argument("--remove-group", metavar="GROUP", help="Remove one saved API group and its managed files")
    api_parser.add_argument("--show-auth-file", action="store_true", help="Print the managed auth snapshot path for the selected group")
    api_parser.add_argument("--import-auth", metavar="PATH", help="Import a user-managed auth.json file into the selected group")
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
            group_name = resolve_chatgpt_group_name(paths, getattr(args, "group", None))
            if handle_chatgpt_group_management(paths, args, group_name):
                return 0
            switch_or_relogin_chatgpt(
                paths,
                codex_bin,
                group_name=group_name,
                relogin=args.relogin,
                device_auth=args.device_auth,
            )
        elif args.command == "api":
            group_name = resolve_api_group_name(paths, getattr(args, "group", None))
            if handle_api_group_management(paths, args, group_name):
                return 0
            switch_or_relogin_api(
                paths,
                codex_bin,
                group_name=group_name,
                base_url=args.base_url,
                relogin=args.relogin,
                prompt_for_key=args.prompt,
                provider_mode=args.provider_mode,
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
