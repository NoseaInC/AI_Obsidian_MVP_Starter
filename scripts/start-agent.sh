#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/00-System/Scripts/.venv/bin/python"
[[ -x "$PYTHON" ]] || PYTHON=python3
cd "$ROOT"
exec "$PYTHON" -m agent.api.server --vault "$ROOT" "${@}"
