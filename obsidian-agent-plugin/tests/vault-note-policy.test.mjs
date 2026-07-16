import assert from "node:assert/strict";
import test from "node:test";
import {build} from "esbuild";

async function moduleUnderTest() {
  const result = await build({
    entryPoints: [new URL("../src/vault-note-policy.ts", import.meta.url).pathname],
    bundle: true, write: false, format: "esm", platform: "node", target: "es2022",
  });
  return import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString("base64")}`);
}

test("assistant note policy only permits knowledge workflow roots", async () => {
  const mod = await moduleUnderTest();
  assert.equal(mod.isAssistantReadableVaultPath("20-Knowledge/Concepts/倾向得分.md"), true);
  assert.equal(mod.isAssistantReadableVaultPath("30-Learning/Daily/2026-07-16.md"), true);
  assert.equal(mod.isAssistantReadableVaultPath("90-Local-Only/private.md"), false);
  assert.equal(mod.isAssistantReadableVaultPath("obsidian-agent-plugin/node_modules/x/README.md"), false);
  assert.equal(mod.isAssistantReadableVaultPath("AGENTS.md"), false);
});

test("explicit note mention resolves to active note context", async () => {
  const mod = await moduleUnderTest();
  assert.equal(
    mod.referencedVaultNotePath("请解释这篇笔记\n@20-Knowledge/Concepts/倾向得分充分性.md"),
    "20-Knowledge/Concepts/倾向得分充分性.md",
  );
  assert.equal(mod.referencedVaultNotePath("@90-Local-Only/private.md"), "");
});
