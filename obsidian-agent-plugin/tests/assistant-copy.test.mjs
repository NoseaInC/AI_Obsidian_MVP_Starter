import test from "node:test";
import assert from "node:assert/strict";
import {build} from "esbuild";
import {readFile} from "node:fs/promises";

async function clipboardModule() {
  const result = await build({
    entryPoints: [new URL("../src/clipboard.ts", import.meta.url).pathname],
    bundle: true,
    write: false,
    format: "esm",
    platform: "node",
    target: "es2022",
  });
  return import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString("base64")}`);
}

async function markdownModule() {
  const result = await build({
    entryPoints: [new URL("../src/markdown-renderer.ts", import.meta.url).pathname],
    bundle: true,
    write: false,
    format: "esm",
    platform: "node",
    target: "es2022",
    plugins: [{
      name: "obsidian-stub",
      setup(builder) {
        builder.onResolve({filter: /^obsidian$/}, () => ({path: "obsidian", namespace: "stub"}));
        builder.onLoad({filter: /.*/, namespace: "stub"}, () => ({
          contents: "export class App {}; export class Component {}; export const MarkdownRenderer = {render: async () => {}};",
          loader: "js",
        }));
      },
    }],
  });
  return import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString("base64")}`);
}

async function withGlobals(values, action) {
  const originals = new Map();
  for (const [name, value] of Object.entries(values)) {
    originals.set(name, Object.getOwnPropertyDescriptor(globalThis, name));
    Object.defineProperty(globalThis, name, {configurable: true, writable: true, value});
  }
  try {
    return await action();
  } finally {
    for (const [name, descriptor] of originals) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
}

const markdownFixture = `# 标题

- 项目

\`\`\`python
print("hello")
\`\`\`

公式：$e(X)=P(T=1\\mid X)$

[来源](https://example.com/source)`;

test("Clipboard API receives the complete original Markdown", async () => {
  const mod = await clipboardModule();
  let copied = "";
  await withGlobals({
    navigator: {clipboard: {writeText: async text => { copied = text; }}},
    document: undefined,
  }, async () => {
    await mod.copyText(markdownFixture);
  });
  assert.equal(copied, markdownFixture);
});

test("stream copy resolves its source at click time instead of keeping a snapshot", async () => {
  const mod = await clipboardModule();
  let current = "第一段";
  let copied = "";
  await withGlobals({
    navigator: {clipboard: {writeText: async text => { copied = text; }}},
    document: undefined,
  }, async () => {
    const source = () => current;
    current = "第一段\n\n第二段";
    await mod.copyTextFrom(source);
  });
  assert.equal(copied, "第一段\n\n第二段");
});

test("failed Clipboard API uses a hidden textarea and always removes it", async () => {
  const mod = await clipboardModule();
  let appended = null;
  let selected = false;
  let removed = false;
  let execCalls = 0;
  let previousFocusRestored = false;
  let selectionCleared = 0;
  const restoredRanges = [];
  const clonedRange = {id: "cloned"};
  const previousActiveElement = {
    focus(options) {
      previousFocusRestored = options?.preventScroll === true;
    },
  };
  const previousSelection = {
    rangeCount: 1,
    getRangeAt(index) {
      assert.equal(index, 0);
      return {cloneRange: () => clonedRange};
    },
    removeAllRanges() { selectionCleared += 1; },
    addRange(range) { restoredRanges.push(range); },
  };
  const style = {
    values: {},
    setProperty(name, value) { this.values[name] = value; },
  };
  const textarea = {
    value: "",
    style,
    setAttribute() {},
    focus() {},
    select() { selected = true; },
    remove() { removed = true; },
  };
  const fakeDocument = {
    activeElement: previousActiveElement,
    getSelection: () => previousSelection,
    body: {appendChild(node) { appended = node; }},
    createElement(name) {
      assert.equal(name, "textarea");
      return textarea;
    },
    execCommand(command) {
      execCalls += 1;
      assert.equal(command, "copy");
      return true;
    },
  };
  await withGlobals({
    navigator: {clipboard: {writeText: async () => { throw new Error("denied"); }}},
    document: fakeDocument,
  }, async () => {
    await mod.copyText(markdownFixture);
  });
  assert.equal(appended, textarea);
  assert.equal(textarea.value, markdownFixture);
  assert.equal(style.values["-webkit-user-select"], "text");
  assert.equal(style.userSelect, "text");
  assert.equal(selected, true);
  assert.equal(execCalls, 1);
  assert.equal(removed, true);
  assert.equal(previousFocusRestored, true);
  assert.equal(selectionCleared, 1);
  assert.deepEqual(restoredRanges, [clonedRange]);
});

test("empty content fails explicitly without touching the clipboard", async () => {
  const mod = await clipboardModule();
  let writes = 0;
  await withGlobals({
    navigator: {clipboard: {writeText: async () => { writes += 1; }}},
    document: undefined,
  }, async () => {
    await assert.rejects(mod.copyText(""), /clipboard_text_empty/);
  });
  assert.equal(writes, 0);
});

test("message controls copy source Markdown only and remain available while streaming", async () => {
  const views = await readFile(new URL("../src/views.ts", import.meta.url), "utf8");
  const liveStart = views.indexOf('const assistant = messages.createDiv({cls: "la-message la-message--assistant la-message--streaming"})');
  const queryStart = views.indexOf("for await (const update of runPiAssistantTurn", liveStart);
  const liveControls = views.slice(liveStart, queryStart);
  assert.match(liveControls, /"复制回答"/);
  assert.match(liveControls, /\(\) => liveRun\.content/);
  assert.match(views, /liveCopyControl\.sync\(\)/);
  assert.match(views, /\(\) => String\(message\.content \?\? ""\)/);
  assert.match(views, /await copyTextFrom\(source\)/);
  assert.doesNotMatch(views.slice(views.indexOf("private renderCopyAction"), views.indexOf("private renderProviderReasoning")), /innerText|reasoningBlocks|toolCalls|metadata/);
  assert.match(views, /button\.disabled = source\(\)\.length === 0/);
  assert.match(views, /new Notice\(successMessage\)/);
  assert.match(views, /复制失败：/);
});

test("answer text is selectable while actions and avatars are not", async () => {
  const css = await readFile(new URL("../styles.css", import.meta.url), "utf8");
  assert.match(css, /\.la-message-copy,[\s\S]*\.la-study-assistant-message\s*\{[\s\S]*-webkit-user-select:\s*text;[\s\S]*user-select:\s*text;[\s\S]*cursor:\s*text;/);
  assert.match(css, /\.la-message-actions,[\s\S]*\.la-message-avatar\s*\{[\s\S]*-webkit-user-select:\s*none;[\s\S]*user-select:\s*none;/);
  assert.doesNotMatch(css, /\.la-app\s*\{[^}]*user-select:\s*text/s);
});

test("progressive rendering defers replacement during selection and commits the latest revision", async () => {
  const mod = await markdownModule();
  const nativeWindow = {
    setTimeout: (callback, delay) => setTimeout(callback, delay),
    clearTimeout: timer => clearTimeout(timer),
  };
  let selecting = true;
  const anchorNode = {outside: true};
  const focusNode = {inside: true};
  const container = {
    ownerDocument: {
      getSelection: () => ({
        isCollapsed: !selecting,
        anchorNode,
        focusNode,
      }),
    },
    contains: node => node === focusNode,
  };
  const commits = [];
  const renderer = {
    async renderAtomic(_container, markdown, _path, _owner, shouldCommit) {
      if (!shouldCommit()) return false;
      commits.push(markdown);
      return true;
    },
  };
  await withGlobals({window: nativeWindow}, async () => {
    const progressive = new mod.ProgressiveAssistantMarkdown(renderer, container, 5, 80);
    progressive.push("旧 revision");
    await new Promise(resolve => setTimeout(resolve, 25));
    assert.deepEqual(commits, []);
    progressive.push("最新 revision");
    selecting = false;
    await progressive.flush();
    assert.deepEqual(commits, ["最新 revision"]);
    progressive.dispose();
  });
});

test("progressive rendering cannot remain paused beyond its selection budget", async () => {
  const mod = await markdownModule();
  const nativeWindow = {
    setTimeout: (callback, delay) => setTimeout(callback, delay),
    clearTimeout: timer => clearTimeout(timer),
  };
  const anchorNode = {};
  const container = {
    ownerDocument: {
      getSelection: () => ({isCollapsed: false, anchorNode, focusNode: anchorNode}),
    },
    contains: node => node === anchorNode,
  };
  const commits = [];
  const renderer = {
    async renderAtomic(_container, markdown, _path, _owner, shouldCommit) {
      if (!shouldCommit()) return false;
      commits.push(markdown);
      return true;
    },
  };
  await withGlobals({window: nativeWindow}, async () => {
    const progressive = new mod.ProgressiveAssistantMarkdown(renderer, container, 5, 35);
    await progressive.flush("最终内容");
    assert.deepEqual(commits, ["最终内容"]);
    progressive.dispose();
  });
});

test("reasoning has an independent copy action while answer copy remains answer-only", async () => {
  const [views, stream] = await Promise.all([
    readFile(new URL("../src/views.ts", import.meta.url), "utf8"),
    readFile(new URL("../src/assistant-stream.ts", import.meta.url), "utf8"),
  ]);
  assert.match(views, /保存在本地用于恢复，不会并入最终回答或未来模型上下文/);
  assert.match(stream, /reasoningBlocks:[\s\S]{0,220}content:\s*string/);
  assert.match(views, /\(\) => String\(message\.content \?\? ""\)/);
  assert.match(views, /"复制思考"/);
  assert.match(views, /visible\.map\(block => String\(block\.content \?\? ""\)\)/);
  assert.doesNotMatch(views, /复制回答与思考/);
});
