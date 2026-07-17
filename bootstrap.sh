#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-${REG_FACTORY_ACTION:-install}}"
INSTALL_DIR="${REG_FACTORY_DIR:-$HOME/reg-factory}"
REPO="https://github.com/tiantianGPU/reg-factory.git"
ARCHIVE="https://github.com/tiantianGPU/reg-factory/archive/refs/heads/main.tar.gz"

install_repository() {
  if [ -d "$INSTALL_DIR/.git" ]; then
    git -C "$INSTALL_DIR" pull --ff-only
    return
  fi
  if [ -d "$INSTALL_DIR" ] && [ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
    echo "Install directory exists and is not a git checkout: $INSTALL_DIR" >&2
    exit 1
  fi
  if command -v git >/dev/null 2>&1; then
    git clone "$REPO" "$INSTALL_DIR"
    return
  fi
  command -v curl >/dev/null 2>&1 || {
    echo "git or curl is required" >&2
    exit 1
  }
  mkdir -p "$INSTALL_DIR"
  tmp="$(mktemp)"
  trap 'rm -f "$tmp"' EXIT
  curl -fL "$ARCHIVE" -o "$tmp"
  tar -xzf "$tmp" --strip-components=1 -C "$INSTALL_DIR"
}

case "$ACTION" in
  install)
    install_repository
    bash "$INSTALL_DIR/install.sh"
    echo "Installed at $INSTALL_DIR"
    ;;
  start)
    if [ ! -f "$INSTALL_DIR/start.sh" ]; then
      echo "reg-factory is not installed at $INSTALL_DIR. Run install first." >&2
      exit 1
    fi
    exec bash "$INSTALL_DIR/start.sh"
    ;;
  *)
    echo "Action must be install or start" >&2
    exit 2
    ;;
esac
