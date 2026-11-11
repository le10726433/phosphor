#!/bin/zsh
set -e
cd "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  echo "Create the development environment first; see docs/MACOS_DEVELOPMENT.md."
  exit 1
fi
exec .venv/bin/python app.py "$@"
