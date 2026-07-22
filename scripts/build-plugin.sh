#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
npm --prefix "$ROOT/obsidian-agent-plugin" test -- --test-timeout=30000
npm --prefix "$ROOT/obsidian-agent-plugin" run typecheck
npm --prefix "$ROOT/obsidian-agent-plugin" run build
test -s "$ROOT/obsidian-agent-plugin/dist/main.js"
test -s "$ROOT/obsidian-agent-plugin/manifest.json"
test -s "$ROOT/obsidian-agent-plugin/styles.css"
test -s "$ROOT/obsidian-agent-plugin/assets/zhixu-assistant-avatar.png"
test -s "$ROOT/obsidian-agent-plugin/assets/ui/send-button-idle.png"
test -s "$ROOT/obsidian-agent-plugin/assets/ui/send-button-idle-v2.svg"
test -s "$ROOT/obsidian-agent-plugin/assets/model-icons/deepseek-color.svg"
test -s "$ROOT/obsidian-agent-plugin/assets/model-icons/openai.svg"

OBSIDIAN_PLUGIN_DIR="$ROOT/.obsidian/plugins/obsidian-learning-agent"
if [ -d "$OBSIDIAN_PLUGIN_DIR" ]; then
  cp "$ROOT/obsidian-agent-plugin/dist/main.js" "$OBSIDIAN_PLUGIN_DIR/main.js"
  cp "$ROOT/obsidian-agent-plugin/styles.css" "$OBSIDIAN_PLUGIN_DIR/styles.css"
  echo "知序 plugin build verified and synced to $OBSIDIAN_PLUGIN_DIR."
else
  echo "知序 plugin build verified (no Obsidian plugin dir at $OBSIDIAN_PLUGIN_DIR)."
fi
