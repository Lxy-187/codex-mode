#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import json
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass


KEYCHAIN_SERVICE = "codex-openai-api-key"


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
    return config_file.read_text()


def write_config_text(config_file: pathlib.Path, text: str) -> None:
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(text)


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
    line = f'openai_base_url = "{base_url}"'
    if re.search(r"(?m)^openai_base_url\s*=", text):
        text = re.sub(r"(?m)^openai_base_url\s*=.*$", line, text, count=1)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += line + "\n"
    write_config_text(config_file, text)


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


def print_status(paths: Paths, codex_bin: str) -> None:
    auth_mode = read_auth_mode(paths.auth_file)
    base_url = read_openai_base_url(paths.config_file)

    if auth_mode == "chatgpt":
        print("Current mode: ChatGPT", flush=True)
    elif auth_mode == "apikey":
        print("Current mode: API key", flush=True)
        print(f"Base URL: {base_url or 'not set'}", flush=True)
    elif auth_mode:
        print(f"Current mode: {auth_mode}", flush=True)
        if base_url:
            print(f"Base URL: {base_url}", flush=True)
    else:
        print("Current mode: unknown", flush=True)

    codex_login_status(codex_bin)


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex-mode")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status")
    sub.add_parser("chatgpt")

    api_parser = sub.add_parser("api")
    api_parser.add_argument("--base-url")
    api_parser.add_argument("--refresh-auth", action="store_true")

    relogin_parser = sub.add_parser("relogin")
    relogin_sub = relogin_parser.add_subparsers(dest="target", required=True)
    relogin_sub.add_parser("chatgpt")
    relogin_api = relogin_sub.add_parser("api")
    relogin_api.add_argument("--base-url")

    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    paths = build_paths()
    codex_bin = detect_codex_bin()

    try:
        if args.command in (None, "status"):
            print_status(paths, codex_bin)
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
