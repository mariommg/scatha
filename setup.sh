#!/usr/bin/env bash
# Starts the setup wizard:  ./setup.sh   (extra words pass through: ./setup.sh status, ./setup.sh x-login)
repo="$(cd "$(dirname "$0")" && pwd)"
if ! command -v uv >/dev/null 2>&1; then
  echo "Setup runs on uv (a Python tool manager). Install it with:"
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo "then open a new terminal and run setup again."
  exit 1
fi
exec uv run --project "$repo/router" python -m jev_router.onboard "$@"
