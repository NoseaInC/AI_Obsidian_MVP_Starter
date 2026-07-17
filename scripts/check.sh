#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/00-System/Scripts/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="${PYTHON:-python3}"
fi

cd "$ROOT"
"$PYTHON" -m unittest discover -s 00-System/Scripts/tests -v
"$PYTHON" -m py_compile \
  00-System/Scripts/ingest_pdf.py \
  00-System/Scripts/prepared_conversation.py \
  00-System/Scripts/prepared_pdf.py \
  00-System/Scripts/review.py \
  00-System/Scripts/pdf_to_obsidian.py \
  00-System/Scripts/conversation_to_obsidian.py \
  scripts/real_harness_topic_smoke.py

if [[ -f agent/pyproject.toml || -d agent/tests ]]; then
  "$PYTHON" -m compileall -q agent
  "$PYTHON" -m unittest discover -s agent/tests -v
fi

if [[ -f obsidian-agent-plugin/package.json ]]; then
  "$ROOT/scripts/build-plugin.sh"
fi

echo "All offline checks passed."
