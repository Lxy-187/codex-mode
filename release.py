#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import re
import sys
import zipfile


ROOT = pathlib.Path(__file__).resolve().parent
VERSION_FILE = ROOT / "VERSION"
CHANGELOG_FILE = ROOT / "CHANGELOG.md"
DIST_DIR = ROOT / "dist"
ARCHIVE_PREFIX = "codex-mode"
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
SKIP_DIRS = {".git", "__pycache__", "dist", "release"}
SKIP_SUFFIXES = {".pyc"}


class ReleaseError(Exception):
    pass


def read_version() -> str:
    if not VERSION_FILE.exists():
        raise ReleaseError(f"Missing VERSION file: {VERSION_FILE}")
    return VERSION_FILE.read_text(encoding="utf-8").strip()


def write_version(version: str) -> None:
    VERSION_FILE.write_text(version + "\n", encoding="utf-8")


def validate_version(version: str) -> str:
    normalized = version.strip()
    if not SEMVER_RE.match(normalized):
        raise ReleaseError(f"Invalid version: {version}")
    return normalized


def read_changelog() -> str:
    if not CHANGELOG_FILE.exists():
        raise ReleaseError(f"Missing changelog: {CHANGELOG_FILE}")
    return CHANGELOG_FILE.read_text(encoding="utf-8")


def unreleased_bounds(text: str) -> tuple[int, int, int]:
    unreleased_match = re.search(r"(?m)^## Unreleased\s*$", text)
    if not unreleased_match:
        raise ReleaseError("CHANGELOG.md is missing the `## Unreleased` heading.")
    section_start = unreleased_match.end()
    next_heading = re.search(r"(?m)^## ", text[section_start:])
    section_end = section_start + next_heading.start() if next_heading else len(text)
    return unreleased_match.start(), section_start, section_end


def normalize_section_body(body: str) -> str:
    stripped = body.strip("\n")
    if not stripped.strip():
        return ""
    return stripped + "\n\n"


def changelog_has_release(text: str, version: str) -> bool:
    return re.search(rf"(?m)^## {re.escape(version)} - \d{{4}}-\d{{2}}-\d{{2}}\s*$", text) is not None


def update_changelog_release(version: str, date_str: str) -> bool:
    text = read_changelog()
    if changelog_has_release(text, version):
        return False

    _, section_start, section_end = unreleased_bounds(text)
    before = text[:section_start]
    unreleased_body = text[section_start:section_end]
    after = text[section_end:]
    normalized_body = normalize_section_body(unreleased_body)
    release_heading = f"\n## {version} - {date_str}\n\n"

    new_text = before.rstrip("\n") + "\n\n"
    if normalized_body:
        new_text += release_heading + normalized_body
    else:
        new_text += release_heading
    new_text += after.lstrip("\n")
    CHANGELOG_FILE.write_text(new_text, encoding="utf-8")
    return True


def iter_package_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for path in ROOT.rglob("*"):
        if path.is_dir():
            if path.name in SKIP_DIRS:
                continue
            continue
        rel = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.suffix in SKIP_SUFFIXES:
            continue
        files.append(path)
    return sorted(files)


def build_archive(version: str, output_dir: pathlib.Path | None = None) -> pathlib.Path:
    target_dir = output_dir or DIST_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    archive_path = target_dir / f"{ARCHIVE_PREFIX}-v{version}.zip"
    root_prefix = f"{ARCHIVE_PREFIX}-v{version}"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in iter_package_files():
            rel = path.relative_to(ROOT)
            zf.write(path, f"{root_prefix}/{rel.as_posix()}")
    return archive_path


def cmd_show(_: argparse.Namespace) -> int:
    print(read_version())
    return 0


def cmd_prepare(args: argparse.Namespace) -> int:
    version = validate_version(args.version)
    date_str = args.date or dt.date.today().isoformat()
    write_version(version)
    changed = update_changelog_release(version, date_str)
    print(f"VERSION updated to {version}")
    print(f"CHANGELOG release entry: {'created' if changed else 'already present'}")
    return 0


def cmd_package(args: argparse.Namespace) -> int:
    version = validate_version(args.version or read_version())
    output_dir = pathlib.Path(args.output).expanduser().resolve() if args.output else None
    archive_path = build_archive(version, output_dir)
    print(archive_path)
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    version = validate_version(args.version)
    date_str = args.date or dt.date.today().isoformat()
    write_version(version)
    changed = update_changelog_release(version, date_str)
    archive_path = build_archive(version, pathlib.Path(args.output).expanduser().resolve() if args.output else None)
    print(f"VERSION updated to {version}")
    print(f"CHANGELOG release entry: {'created' if changed else 'already present'}")
    print(f"Archive created: {archive_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release.py",
        description="Manage codex-mode versioning, changelog releases, and zip packaging.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    show_parser = sub.add_parser("show", help="Print the current VERSION")
    show_parser.set_defaults(func=cmd_show)

    prepare_parser = sub.add_parser(
        "prepare",
        help="Update VERSION and roll the Unreleased changelog section into one dated release heading",
    )
    prepare_parser.add_argument("version")
    prepare_parser.add_argument("--date", help="Release date in YYYY-MM-DD format")
    prepare_parser.set_defaults(func=cmd_prepare)

    package_parser = sub.add_parser(
        "package",
        help="Create a zip archive from the current repo contents",
    )
    package_parser.add_argument("--version", help="Override the version used in the archive name")
    package_parser.add_argument("--output", help="Output directory, defaults to ./dist")
    package_parser.set_defaults(func=cmd_package)

    release_parser = sub.add_parser(
        "release",
        help="Run prepare + package in one step",
    )
    release_parser.add_argument("version")
    release_parser.add_argument("--date", help="Release date in YYYY-MM-DD format")
    release_parser.add_argument("--output", help="Output directory, defaults to ./dist")
    release_parser.set_defaults(func=cmd_release)

    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ReleaseError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
