import esbuild from "esbuild";
import fs from "fs";
import path from "path";

await esbuild.build({
  entryPoints: ["main.ts"], bundle: true, external: ["obsidian"], format: "cjs",
  platform: "node", target: "es2020", outfile: "dist/main.js", sourcemap: false,
});

// Sync built files to Obsidian plugin directory
const pluginDir = path.resolve("../.obsidian/plugins/obsidian-learning-agent");
fs.copyFileSync("dist/main.js", path.join(pluginDir, "main.js"));
fs.copyFileSync("styles.css", path.join(pluginDir, "styles.css"));
fs.copyFileSync("manifest.json", path.join(pluginDir, "manifest.json"));
console.log("Synced to", pluginDir);
