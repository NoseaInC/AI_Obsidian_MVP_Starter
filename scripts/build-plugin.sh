#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
npm --prefix "$ROOT/obsidian-agent-plugin" test
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
echo "知序 plugin build verified."
