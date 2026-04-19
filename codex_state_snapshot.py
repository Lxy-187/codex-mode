#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import shutil
import sys


FILES_TO_TRACK = ("config.toml", "auth.json")
BACKUP_ROOT_NAME = "manual-backups/codex-state"
LATEST_FILE_NAME = "LATEST"
METADATA_FILE_NAME = "metadata.json"


class SnapshotError(Exception):
    pass


def codex_home() -> pathlib.Path:
    return pathlib.Path(os.environ.get("CODEX_HOME", pathlib.Path.home() / ".codex")).expanduser()


def backup_root() -> pathlib.Path:
    return codex_home() / BACKUP_ROOT_NAME


def now_label() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def tracked_paths() -> dict[str, pathlib.Path]:
    root = codex_home()
    return {name: root / name for name in FILES_TO_TRACK}


def read_latest_name(root: pathlib.Path) -> str:
    latest_file = root / LATEST_FILE_NAME
    if not latest_file.exists():
        raise SnapshotError("No latest snapshot marker exists.")
    name = latest_file.read_text(encoding="utf-8").strip()
    if not name:
        raise SnapshotError("Latest snapshot marker is empty.")
    return name


def snapshot_dir(root: pathlib.Path, name: str | None) -> pathlib.Path:
    snapshot_name = name or read_latest_name(root)
    target = root / snapshot_name
    if not target.exists():
        raise SnapshotError(f"Snapshot does not exist: {target}")
    return target


def ensure_backup_root(root: pathlib.Path) -> None:
    root.mkdir(parents=True, exist_ok=True)


def cmd_backup(label: str | None) -> int:
    root = backup_root()
    ensure_backup_root(root)

    snapshot_name = f"{now_label()}"
    if label:
        safe_label = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in label.strip())
        if safe_label:
            snapshot_name = f"{snapshot_name}-{safe_label}"

    target = root / snapshot_name
    target.mkdir(parents=True, exist_ok=False)

    copied: list[str] = []
    missing: list[str] = []
    for name, source in tracked_paths().items():
        if source.exists():
            shutil.copy2(source, target / name)
            copied.append(name)
        else:
            missing.append(name)

    metadata = {
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "codex_home": str(codex_home()),
        "copied": copied,
        "missing": missing,
    }
    (target / METADATA_FILE_NAME).write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    (root / LATEST_FILE_NAME).write_text(snapshot_name + "\n", encoding="utf-8")

    print(f"Created snapshot: {target}")
    print(f"Tracked files copied: {', '.join(copied) if copied else 'none'}")
    if missing:
        print(f"Tracked files missing at backup time: {', '.join(missing)}")
    print(f"Restore command: python3 {pathlib.Path(__file__).resolve()} restore --name {snapshot_name}")
    return 0


def cmd_restore(name: str | None) -> int:
    root = backup_root()
    target = snapshot_dir(root, name)
    restored: list[str] = []
    missing: list[str] = []

    for file_name in FILES_TO_TRACK:
        source = target / file_name
        dest = codex_home() / file_name
        if source.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
            restored.append(file_name)
        else:
            missing.append(file_name)

    print(f"Restored snapshot: {target}")
    print(f"Restored files: {', '.join(restored) if restored else 'none'}")
    if missing:
        print(f"Snapshot did not contain: {', '.join(missing)}")
    return 0


def cmd_list() -> int:
    root = backup_root()
    if not root.exists():
        print("No snapshots found.")
        return 0

    latest_name = None
    latest_file = root / LATEST_FILE_NAME
    if latest_file.exists():
        latest_name = latest_file.read_text(encoding="utf-8").strip() or None

    snapshots = sorted(
        [path for path in root.iterdir() if path.is_dir()],
        key=lambda p: p.name,
        reverse=True,
    )
    if not snapshots:
        print("No snapshots found.")
        return 0

    for path in snapshots:
        marker = " (latest)" if latest_name == path.name else ""
        print(f"{path.name}{marker}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-state-snapshot",
        description="Backup and restore the current Codex config.toml and auth.json.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    backup_parser = sub.add_parser("backup", help="Create a snapshot of config.toml and auth.json")
    backup_parser.add_argument("--label", help="Optional label appended to the snapshot name")

    restore_parser = sub.add_parser("restore", help="Restore config.toml and auth.json from a snapshot")
    restore_parser.add_argument("--name", help="Snapshot name to restore; defaults to the latest snapshot")

    sub.add_parser("list", help="List available snapshots")
    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "backup":
            return cmd_backup(args.label)
        if args.command == "restore":
            return cmd_restore(args.name)
        if args.command == "list":
            return cmd_list()
    except SnapshotError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    parser.error("unsupported command")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
