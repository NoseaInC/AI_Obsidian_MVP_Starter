#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$ROOT/obsidian-agent-plugin"
TARGET="$ROOT/.obsidian/plugins/obsidian-learning-agent"
BACKUP="$ROOT/90-Local-Only/PluginBackups/obsidian-learning-agent-$(date +%Y%m%d-%H%M%S)"

"$ROOT/scripts/build-plugin.sh"
mkdir -p "$TARGET"
if [[ -f "$TARGET/main.js" || -f "$TARGET/manifest.json" || -f "$TARGET/styles.css" ]]; then
  mkdir -p "$BACKUP"
  for file in main.js manifest.json styles.css; do
    [[ ! -f "$TARGET/$file" ]] || cp "$TARGET/$file" "$BACKUP/$file"
  done
fi
install -m 0644 "$SOURCE/dist/main.js" "$TARGET/main.js"
install -m 0644 "$SOURCE/manifest.json" "$TARGET/manifest.json"
install -m 0644 "$SOURCE/styles.css" "$TARGET/styles.css"
install -d -m 0755 "$TARGET/assets"
install -m 0644 "$SOURCE/assets/zhixu-assistant-avatar.png" "$TARGET/assets/zhixu-assistant-avatar.png"
install -d -m 0755 "$TARGET/assets/ui"
install -m 0644 "$SOURCE/assets/ui/send-button-idle.png" "$TARGET/assets/ui/send-button-idle.png"
install -m 0644 "$SOURCE/assets/ui/send-button-idle-v2.svg" "$TARGET/assets/ui/send-button-idle-v2.svg"
install -d -m 0755 "$TARGET/assets/model-icons"
for icon in "$SOURCE/assets/model-icons/"*.svg; do
  install -m 0644 "$icon" "$TARGET/assets/model-icons/$(basename "$icon")"
done
test -s "$TARGET/main.js"
test -s "$TARGET/assets/zhixu-assistant-avatar.png"
test -s "$TARGET/assets/ui/send-button-idle.png"
test -s "$TARGET/assets/ui/send-button-idle-v2.svg"
test -s "$TARGET/assets/model-icons/deepseek-color.svg"
test -s "$TARGET/assets/model-icons/openai.svg"
cmp -s "$SOURCE/dist/main.js" "$TARGET/main.js"
cmp -s "$SOURCE/assets/zhixu-assistant-avatar.png" "$TARGET/assets/zhixu-assistant-avatar.png"
cmp -s "$SOURCE/assets/ui/send-button-idle.png" "$TARGET/assets/ui/send-button-idle.png"
cmp -s "$SOURCE/assets/ui/send-button-idle-v2.svg" "$TARGET/assets/ui/send-button-idle-v2.svg"
cmp -s "$SOURCE/assets/model-icons/deepseek-color.svg" "$TARGET/assets/model-icons/deepseek-color.svg"
cmp -s "$SOURCE/assets/model-icons/openai.svg" "$TARGET/assets/model-icons/openai.svg"
echo "Installed 知序 at $TARGET"
echo "Reload the plugin from Obsidian settings to activate this build."
