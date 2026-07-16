import test from "node:test";
import assert from "node:assert/strict";
import {build} from "esbuild";

async function moduleUnderTest() {
  const result = await build({
    entryPoints: [new URL("../src/markdown-normalize.ts", import.meta.url).pathname],
    bundle: true, write: false, format: "esm", platform: "node", target: "es2022",
  });
  return import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString("base64")}`);
}

test("normalizes model inline and display math to Obsidian delimiters", async () => {
  const {normalizeAssistantMarkdown} = await moduleUnderTest();
  const value = normalizeAssistantMarkdown("定义 \\(e(x)=P(T=1|X)\\)。\\[ATE=E[Y(1)-Y(0)]\\]");
  assert.equal(value, "定义 $e(x)=P(T=1|X)$。$$\nATE=E[Y(1)-Y(0)]\n$$");
});

test("does not rewrite code fences inline code URLs or existing dollar math", async () => {
  const {normalizeAssistantMarkdown} = await moduleUnderTest();
  const source = "`\\(code\\)`\n```tex\n\\[code block\\]\n```\nhttps://example.com/\\(path\\)\n$x+1$\n$$y+2$$";
  assert.equal(normalizeAssistantMarkdown(source), source);
});

test("handles mixed markdown without emitting unsafe HTML", async () => {
  const {normalizeAssistantMarkdown} = await moduleUnderTest();
  const source = "- 条件：\\(X \\perp T\\)\n\n**结论**：保持 Markdown。";
  const result = normalizeAssistantMarkdown(source);
  assert.match(result, /\$X \\perp T\$/);
  assert.doesNotMatch(result, /<script|innerHTML/i);
});
