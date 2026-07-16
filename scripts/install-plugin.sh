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
test -s "$TARGET/main.js"
cmp -s "$SOURCE/dist/main.js" "$TARGET/main.js"
echo "Installed 知序 at $TARGET"
echo "Reload the plugin from Obsidian settings to activate this build."
