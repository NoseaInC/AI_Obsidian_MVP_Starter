import test from "node:test";
import assert from "node:assert/strict";
import {build} from "esbuild";
import {readFile} from "node:fs/promises";
import {fileURLToPath} from "node:url";
import * as path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

async function appearanceModule() {
  const result = await build({
    entryPoints: [path.join(__dirname, "..", "src", "appearance.ts")],
    bundle: true,
    write: false,
    format: "esm",
    platform: "node",
    target: "es2022",
  });
  return import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString("base64")}`);
}

test("default settings", async () => {
  const mod = await appearanceModule();
  const s = mod.DEFAULT_APPEARANCE_SETTINGS;
  assert.equal(s.uiFontPreset, "obsidian");
  assert.equal(s.readingFontPreset, "obsidian");
  assert.equal(s.readingFontSize, 15);
  assert.equal(s.readingLineHeight, 1.7);
  assert.equal(s.uiFontCustom, "");
  assert.equal(s.readingFontCustom, "");
});

test("font stack resolution — harmonyos-sans-sc", () => {
  // Import the module inline via the bundled version; we use the
  // same resolveZhixuFontStack function but need to call it through
  // the module interface. We test the expected substrings in the
  // resolved font stack.
  // Since we can't directly call resolveZhixuFontStack without
  // bundling, we verify that applyZhixuAppearance produces
  // the correct CSS variable values.
});

test("font stack resolution — harmonyos-sans-sc contains expected families", async () => {
  const mod = await appearanceModule();
  const style = new MockCSSStyleDeclaration();
  mod.applyZhixuAppearance(style, {
    uiFontPreset: "harmonyos-sans-sc",
    uiFontCustom: "",
    readingFontPreset: "obsidian",
    readingFontCustom: "",
    readingFontSize: 15,
    readingLineHeight: 1.7,
  });
  const uiFont = style.get("--zhixu-ui-font");
  assert.ok(uiFont.includes("HarmonyOS Sans SC"), "should contain HarmonyOS Sans SC");
  assert.ok(uiFont.includes("PingFang SC"), "should contain PingFang SC");
  assert.ok(uiFont.includes("sans-serif"), "should contain sans-serif");
});

test("font stack resolution — lxgw-wenkai contains expected families", async () => {
  const mod = await appearanceModule();
  const style = new MockCSSStyleDeclaration();
  mod.applyZhixuAppearance(style, {
    uiFontPreset: "obsidian",
    uiFontCustom: "",
    readingFontPreset: "lxgw-wenkai",
    readingFontCustom: "",
    readingFontSize: 15,
    readingLineHeight: 1.7,
  });
  const readingFont = style.get("--zhixu-reading-font");
  assert.ok(readingFont.includes("LXGW WenKai"), "should contain LXGW WenKai");
  assert.ok(readingFont.includes("霞鹜文楷"), "should contain 霞鹜文楷");
  assert.ok(readingFont.includes("PingFang SC"), "should contain PingFang SC");
});

test("custom font sanitization — strips dangerous characters", async () => {
  const mod = await appearanceModule();
  const bad = "My Font\"; color:red; {";
  const sanitized = mod.sanitizeCustomFontName(bad);
  assert.ok(!sanitized.includes(";"), "should not contain semicolon");
  assert.ok(!sanitized.includes("{"), "should not contain brace");
  assert.ok(!sanitized.includes("}"), "should not contain closing brace");
  assert.ok(!sanitized.includes(":"), "should not contain colon");
  assert.ok(!sanitized.includes("\""), "should not contain double quote");
  assert.ok(!sanitized.includes("'"), "should not contain single quote");
});

test("custom font sanitization — preserves valid name", async () => {
  const mod = await appearanceModule();
  const name = mod.sanitizeCustomFontName("  PingFang   SC  ");
  assert.equal(name, "PingFang SC");
});

test("custom font sanitization — truncates long names", async () => {
  const mod = await appearanceModule();
  const long = "A".repeat(200);
  const result = mod.sanitizeCustomFontName(long);
  assert.ok(result.length <= 80);
});

test("numeric clamping — fontSize", async () => {
  const mod = await appearanceModule();
  const style = new MockCSSStyleDeclaration();
  mod.applyZhixuAppearance(style, {
    uiFontPreset: "obsidian",
    uiFontCustom: "",
    readingFontPreset: "obsidian",
    readingFontCustom: "",
    readingFontSize: 8, // below min
    readingLineHeight: 1.7,
  });
  assert.equal(style.get("--zhixu-reading-size"), "13px", "should clamp to 13px");
});

test("numeric clamping — fontSize upper bound", async () => {
  const mod = await appearanceModule();
  const style = new MockCSSStyleDeclaration();
  mod.applyZhixuAppearance(style, {
    uiFontPreset: "obsidian",
    uiFontCustom: "",
    readingFontPreset: "obsidian",
    readingFontCustom: "",
    readingFontSize: 25, // above max
    readingLineHeight: 1.7,
  });
  assert.equal(style.get("--zhixu-reading-size"), "19px", "should clamp to 19px");
});

test("numeric clamping — lineHeight lower bound", async () => {
  const mod = await appearanceModule();
  const style = new MockCSSStyleDeclaration();
  mod.applyZhixuAppearance(style, {
    uiFontPreset: "obsidian",
    uiFontCustom: "",
    readingFontPreset: "obsidian",
    readingFontCustom: "",
    readingFontSize: 15,
    readingLineHeight: 0.5, // below min
  });
  assert.equal(style.get("--zhixu-reading-line-height"), "1.4", "should clamp to 1.4");
});

test("numeric clamping — lineHeight upper bound", async () => {
  const mod = await appearanceModule();
  const style = new MockCSSStyleDeclaration();
  mod.applyZhixuAppearance(style, {
    uiFontPreset: "obsidian",
    uiFontCustom: "",
    readingFontPreset: "obsidian",
    readingFontCustom: "",
    readingFontSize: 15,
    readingLineHeight: 3, // above max
  });
  assert.equal(style.get("--zhixu-reading-line-height"), "2", "should clamp to 2");
});

test("CSS variable application", async () => {
  const mod = await appearanceModule();
  const style = new MockCSSStyleDeclaration();
  mod.applyZhixuAppearance(style, {
    uiFontPreset: "harmonyos-sans-sc",
    uiFontCustom: "",
    readingFontPreset: "lxgw-wenkai",
    readingFontCustom: "",
    readingFontSize: 16,
    readingLineHeight: 1.8,
  });
  assert.ok(style.get("--zhixu-ui-font"), "--zhixu-ui-font should be set");
  assert.ok(style.get("--zhixu-reading-font"), "--zhixu-reading-font should be set");
  assert.equal(style.get("--zhixu-reading-size"), "16px");
  assert.equal(style.get("--zhixu-reading-line-height"), "1.8");
});

test("clearZhixuAppearance removes all variables", async () => {
  const mod = await appearanceModule();
  const style = new MockCSSStyleDeclaration();
  mod.applyZhixuAppearance(style, {
    uiFontPreset: "harmonyos-sans-sc",
    uiFontCustom: "",
    readingFontPreset: "lxgw-wenkai",
    readingFontCustom: "",
    readingFontSize: 16,
    readingLineHeight: 1.8,
  });
  mod.clearZhixuAppearance(style);
  assert.equal(style.get("--zhixu-ui-font"), undefined);
  assert.equal(style.get("--zhixu-reading-font"), undefined);
  assert.equal(style.get("--zhixu-reading-size"), undefined);
  assert.equal(style.get("--zhixu-reading-line-height"), undefined);
});

test("old settings migration — missing fields get defaults", async () => {
  const mod = await appearanceModule();
  const oldData = {
    port: 8765,
    pythonPath: "",
    autoStart: true,
    stopOnUnload: true,
    behaviorPersonalization: true,
    recordLearningDuration: true,
    useQuizResults: true,
    useRecommendationFeedback: true,
    useAssistantSummaries: false,
    useRecentMaterials: true,
    dailyKnowledgeCount: 1,
    trustedResearch: true,
    openWebResearch: false,
    behaviorTrackingPaused: false,
  };
  const merged = Object.assign({}, mod.DEFAULT_APPEARANCE_SETTINGS, oldData);
  // Old fields preserved
  assert.equal(merged.port, 8765);
  // New appearance fields filled in
  assert.equal(merged.uiFontPreset, "obsidian");
  assert.equal(merged.readingFontPreset, "obsidian");
  assert.equal(merged.readingFontSize, 15);
  assert.equal(merged.readingLineHeight, 1.7);
});

test("CSS scope — styles.css does not have body font-family override", async () => {
  const css = await readFile(path.join(__dirname, "..", "styles.css"), "utf-8");
  assert.ok(!/body\s*\{[^}]*font-family\s*:/.test(css), "styles.css should not set font-family on body");
});

test("CSS scope — styles.css does not use @font-face", async () => {
  const css = await readFile(path.join(__dirname, "..", "styles.css"), "utf-8");
  assert.ok(!css.includes("@font-face"), "styles.css should not contain @font-face");
});

test("CSS scope — styles.css does not contain font remote URL", async () => {
  const css = await readFile(path.join(__dirname, "..", "styles.css"), "utf-8");
  assert.ok(!css.includes("url("), "styles.css should not contain url() for fonts");
});

test("CSS scope — .la-app uses --zhixu-ui-font", async () => {
  const css = await readFile(path.join(__dirname, "..", "styles.css"), "utf-8");
  assert.ok(css.includes("--zhixu-ui-font"), "styles.css should reference --zhixu-ui-font");
});

test("CSS scope — reading areas use --zhixu-reading-font", async () => {
  const css = await readFile(path.join(__dirname, "..", "styles.css"), "utf-8");
  assert.ok(css.includes("--zhixu-reading-font"), "styles.css should reference --zhixu-reading-font");
});

test("CSS scope — code uses --font-monospace", async () => {
  const css = await readFile(path.join(__dirname, "..", "styles.css"), "utf-8");
  assert.ok(css.includes("--font-monospace"), "styles.css should preserve monospace for code");
});

test("CSS scope — no .la-app * font-family override", async () => {
  const css = await readFile(path.join(__dirname, "..", "styles.css"), "utf-8");
  // Should not have .la-app * with font-family
  const match = css.match(/\.la-app\s+\*\s*\{[^}]*font-family/);
  assert.equal(match, null, "should not override font-family on .la-app *");
});

// Minimal mock for CSSStyleDeclaration
class MockCSSStyleDeclaration {
  #properties = new Map();

  setProperty(name, value) {
    this.#properties.set(name, value);
  }

  removeProperty(name) {
    this.#properties.delete(name);
  }

  get(name) {
    return this.#properties.get(name);
  }
}
