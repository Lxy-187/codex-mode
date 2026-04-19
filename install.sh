#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TARGET_DIR=${1:-"$HOME/.local/bin"}

mkdir -p "$TARGET_DIR"

install -m 755 "$SCRIPT_DIR/codex-mode" "$TARGET_DIR/codex-mode"
install -m 755 "$SCRIPT_DIR/codex_mode.py" "$TARGET_DIR/codex_mode.py"

printf 'Installed:\n'
printf '  %s\n' "$TARGET_DIR/codex-mode"
printf '  %s\n' "$TARGET_DIR/codex_mode.py"

case ":${PATH:-}:" in
  *:"$TARGET_DIR":*)
    printf 'PATH already contains %s\n' "$TARGET_DIR"
    ;;
  *)
    printf 'Add this to your shell config if needed:\n'
    printf '  export PATH="%s:$PATH"\n' "$TARGET_DIR"
    ;;
esac
