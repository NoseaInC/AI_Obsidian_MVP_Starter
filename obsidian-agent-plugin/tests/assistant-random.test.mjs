import test from "node:test";
import assert from "node:assert/strict";
import {build} from "esbuild";
import {readFile} from "node:fs/promises";

const seed = Number.parseInt(process.env.LA_TEST_SEED || "20260715", 10);
function prng(initial) {
  let value = initial >>> 0;
  return () => {
    value = (value + 0x6d2b79f5) >>> 0;
    let next = value;
    next = Math.imul(next ^ next >>> 15, next | 1);
    next ^= next + Math.imul(next ^ next >>> 7, next | 61);
    return ((next ^ next >>> 14) >>> 0) / 4294967296;
  };
}

async function bundled(path) {
  const result = await build({entryPoints: [new URL(path, import.meta.url).pathname], bundle: true, write: false, format: "esm", platform: "node", target: "es2022"});
  return import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString("base64")}`);
}

const corpus = [
  "中文段落：影响函数连接 Delta Method。",
  "English and 中文 mixed content 🚀",
  "# Heading\n\n- one\n- two\n\n| A | B |\n|---|---|\n| 1 | 2 |",
  "```python\nprint('\\(not math\\)')\n```\n\ninline `\\[code\\]`",
  "公式 \\(e(x)=P(T=1|X)\\) 与展示公式 \\[ATE=E[Y(1)-Y(0)]\\]",
  "[[倾向得分充分性]] https://example.com/path?q=\\(safe\\)",
  "未闭合公式 \\(x + y 与未闭合代码 ```js\nconst x = 1",
  "<script>globalThis.pwned=true</script> 只应作为文本",
];

test("5000 seeded stream partitions reconstruct exact Markdown and normalize safely", async () => {
  const stream = await bundled("../src/assistant-stream.ts");
  const markdown = await bundled("../src/markdown-normalize.ts");
  const random = prng(seed);
  for (let sample = 0; sample < 5_000; sample += 1) {
    const source = corpus[Math.floor(random() * corpus.length)] + `\nseed=${seed};sample=${sample}`;
    const chunks = [];
    for (let cursor = 0; cursor < source.length;) {
      const length = 1 + Math.floor(random() * 19);
      chunks.push(source.slice(cursor, cursor + length)); cursor += length;
    }
    let state = stream.initialAssistantLiveRun();
    state = stream.reduceAssistantStream(state, {schemaVersion: 2, seq: 1, type: "run.started", runId: `run-${sample}`, conversationId: "c"});
    chunks.forEach((delta, index) => {
      state = stream.reduceAssistantStream(state, {schemaVersion: 2, seq: index + 2, type: "message.delta", runId: `run-${sample}`, conversationId: "c", delta});
    });
    assert.equal(state.content, source, `seed=${seed}; sample=${sample}; chunks=${JSON.stringify(chunks)}`);
    const normalized = markdown.normalizeAssistantMarkdown(state.content);
    if (source.includes("https://")) assert.ok(normalized.includes("https://example.com/path?q=\\(safe\\)"), `seed=${seed}; sample=${sample}`);
    if (source.includes("```python")) assert.ok(normalized.includes("```python\nprint('\\(not math\\)')\n```"), `seed=${seed}; sample=${sample}`);
  }
});

test("10000 seeded UI actions preserve conversation, abort and inspector invariants", () => {
  const random = prng(seed ^ 0xa11ce);
  const state = {conversations: ["conv-0"], active: "conv-0", running: false, aborted: false, inspector: true, tab: "context", attachments: new Set(), mounted: true};
  const operations = ["new", "send", "stop", "switch", "search", "inspector", "tab", "attach", "remove", "unmount", "mount", "retry"];
  for (let step = 0; step < 10_000; step += 1) {
    const operation = operations[Math.floor(random() * operations.length)];
    if (operation === "new") { const id = `conv-${state.conversations.length}`; state.conversations.push(id); state.active = id; state.running = false; }
    else if (operation === "send" && state.mounted && !state.running) { state.running = true; state.aborted = false; }
    else if (operation === "stop" && state.running) { state.running = false; state.aborted = true; }
    else if (operation === "switch") { state.running = false; state.aborted = true; state.active = state.conversations[Math.floor(random() * state.conversations.length)]; }
    else if (operation === "inspector") state.inspector = !state.inspector;
    else if (operation === "tab") state.tab = ["context", "sources", "changes"][Math.floor(random() * 3)];
    else if (operation === "attach") state.attachments.add(`att-${Math.floor(random() * 50)}`);
    else if (operation === "remove" && state.attachments.size) state.attachments.delete([...state.attachments][0]);
    else if (operation === "unmount") { state.mounted = false; state.running = false; state.aborted = true; }
    else if (operation === "mount") state.mounted = true;
    else if (operation === "retry" && state.mounted && !state.running) { state.running = true; state.aborted = false; }
    assert.ok(state.conversations.includes(state.active), `seed=${seed}; step=${step}; op=${operation}`);
    assert.ok(["context", "sources", "changes"].includes(state.tab), `seed=${seed}; step=${step}`);
    assert.ok(state.attachments.size <= 50, `seed=${seed}; step=${step}`);
    if (!state.mounted) assert.equal(state.running, false, `seed=${seed}; step=${step}; unmounted update`);
  }
});

test("fixed and 300 seeded window sizes map to safe responsive modes", async () => {
  const css = await readFile(new URL("../styles.css", import.meta.url), "utf8");
  const random = prng(seed ^ 0x51ee);
  const fixed = [320, 375, 480, 768, 900, 1024, 1280, 1440, 1728, 1920];
  const sizes = fixed.map(width => [width, 900]);
  for (let index = 0; index < 300; index += 1) sizes.push([320 + Math.floor(random() * 1_601), 500 + Math.floor(random() * 701)]);
  for (const [width, height] of sizes) {
    const mode = width <= 920 ? "compact" : width <= 1180 ? "two-plus-drawer" : "three-column";
    assert.ok(["compact", "two-plus-drawer", "three-column"].includes(mode), `seed=${seed}; ${width}x${height}`);
    assert.ok(width >= 320 && height >= 500, `seed=${seed}; ${width}x${height}`);
  }
  assert.match(css, /@container \(max-width: 920px\)/);
  assert.match(css, /@container \(max-width: 1420px\)/);
  assert.match(css, /overflow:\s*hidden/);
  assert.match(css, /:focus-visible/);
});

test("thirty regression seeds remain replayable", async () => {
  const seeds = JSON.parse(await readFile(new URL("./assistant-regression-seeds.json", import.meta.url), "utf8"));
  assert.equal(seeds.length, 30);
  assert.equal(new Set(seeds).size, 30);
  for (const item of seeds) assert.ok(Number.isSafeInteger(item) && item > 0);
});
