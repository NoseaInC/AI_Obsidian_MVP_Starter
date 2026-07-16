# Obsidian 知序 plugin

1. Start the local service with `./scripts/start-agent.sh`.
2. Run `npm install && npm run build` in this directory.
3. Create `.obsidian/plugins/obsidian-learning-agent/` and copy `manifest.json` plus `dist/main.js` into it.
4. Enable “Obsidian 知序” in Community plugins.

The plugin connects only to `http://127.0.0.1:8765`. It stores no API key and never auto-approves a Change Set.
