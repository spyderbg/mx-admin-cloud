#!/usr/bin/env bash
# Install or uninstall the Kiddo CLI as the terminal command "kiddo-cli".
# Usage: cli.sh install | uninstall

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CLI_DIR="$REPO_ROOT/cli"

case "${1:-}" in
  install)
    if [[ ! -d "$CLI_DIR" ]]; then
      echo "Error: CLI directory not found: $CLI_DIR" >&2
      exit 1
    fi
    pip install -e "$CLI_DIR"
    echo "Installed. Run 'kiddo-cli --help' to use the CLI."
    ;;
  uninstall)
    pip uninstall -y kiddo-cli 2>/dev/null || true
    echo "Uninstalled kiddo-cli."
    ;;
  *)
    echo "Usage: $0 install | uninstall" >&2
    exit 1
    ;;
esac
