import assert from "node:assert/strict";
import {mkdtemp, mkdir, readFile, readdir, rename, rm, writeFile} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {createRequire} from "node:module";
import test from "node:test";
import esbuild from "esbuild";

async function fixture() {
  const directory = await mkdtemp(path.join(os.tmpdir(), "zhixu-pi-repair-acceptance-"));
  const vault = path.join(directory, "Vault");
  await mkdir(path.join(vault, "20-Knowledge", "Concepts"), {recursive: true});
  await mkdir(path.join(vault, "90-Local-Only", "Agent"), {recursive: true});
  await writeFile(
    path.join(vault, "20-Knowledge", "current.md"),
    "# 当前笔记\n\n识别依赖一致性、无混杂与重叠性。\n",
    "utf8",
  );
  await writeFile(
    path.join(vault, "20-Knowledge", "Concepts", "倾向得分.md"),
    "# 倾向得分\n\n倾向得分用于平衡处理组与对照组，仍需检查重叠性。\n",
    "utf8",
  );
  await writeFile(path.join(vault, "90-Local-Only", "Agent", "acceptance.sqlite3"), "", "utf8");
  const outfile = path.join(directory, "runtime.cjs");
  const helperOutfile = path.join(directory, "assistant-turn.cjs");
  await Promise.all([
    esbuild.build({
      entryPoints: [path.resolve("src/core/runtime/PiAgentRuntime.ts")],
      outfile,
      bundle: true,
      format: "cjs",
      platform: "node",
      target: "node22",
      logLevel: "silent",
    }),
    esbuild.build({
      entryPoints: [path.resolve("src/run-pi-assistant-turn.ts")],
      outfile: helperOutfile,
      bundle: true,
      format: "cjs",
      platform: "node",
      target: "node22",
      logLevel: "silent",
    }),
  ]);
  const require = createRequire(import.meta.url);
  return {
    PiAgentRuntime: require(outfile).PiAgentRuntime,
    runPiAssistantTurn: require(helperOutfile).runPiAssistantTurn,
    vault,
    dispose: () => rm(directory, {recursive: true, force: true}),
  };
}

function contract(name, description) {
  return {
    name,
    description,
    input_schema: {type: "object", properties: {}, additionalProperties: true},
    output_schema: {type: "object", additionalProperties: true},
    uses_network: false,
    mutates_state: false,
    timeout_seconds: 20,
    permission_level: "read_only",
    idempotent: true,
    cancellable: false,
    max_result_bytes: 100_000,
  };
}

function emitText(onEvent, text) {
  onEvent({type: "start"});
  onEvent({type: "text_start"});
  onEvent({type: "text_delta", delta: text});
  onEvent({type: "text_end"});
  onEvent({type: "done", finishReason: "stop"});
}

function emitTool(onEvent, id, name, args) {
  const encoded = JSON.stringify(args);
  onEvent({type: "start"});
  onEvent({type: "tool_call_start", index: 0, id, name});
  onEvent({type: "tool_call_delta", index: 0, delta: encoded});
  onEvent({type: "tool_call_end", index: 0, id, name, arguments: encoded});
  onEvent({type: "done", finishReason: "toolUse"});
}

function baseTransport(overrides = {}) {
  return {
    async registerTaskAuthorization(body) {
      return {taskAuthorization: body.taskAuthorization};
    },
    async appendRuntimeEvents(body) {
      return {lastEventSequence: body.events.at(-1)?.sequence ?? 0};
    },
    async toolContracts() {
      return {schemaVersion: 1, items: []};
    },
    ...overrides,
  };
}

async function consume(runtime, request) {
  const chunks = [];
  for await (const chunk of runtime.query(runtime.prepareTurn(request))) chunks.push(chunk);
  assert.equal(chunks.at(-1)?.type, "done");
  assert.equal(chunks.at(-1)?.status, "completed");
  return chunks;
}

test("repair acceptance A: ordinary Q&A completes in the production Pi loop", async () => {
  const current = await fixture();
  let requests = 0;
  const runtime = new current.PiAgentRuntime(baseTransport({
    async streamModelProxy(_body, onEvent) {
      requests += 1;
      emitText(onEvent, "普通问答已由 Pi 完成。");
    },
  }));
  try {
    const chunks = await consume(runtime, {
      message: "什么是倾向得分？",
      conversationId: "repair-acceptance-a",
    });
    assert.equal(requests, 1);
    assert.equal(chunks.filter(item => item.type === "text").map(item => item.content).join(""), "普通问答已由 Pi 完成。");
  } finally {
    runtime.cleanup();
    await current.dispose();
  }
});

test("repair acceptance B: current-note Q&A reads the real temporary note", async () => {
  const current = await fixture();
  let requests = 0;
  const runtime = new current.PiAgentRuntime(baseTransport({
    async toolContracts() {
      return {schemaVersion: 1, items: [contract("get_current_note", "Read the current note")]};
    },
    async callRuntimeTool(body) {
      assert.equal(body.toolName, "get_current_note");
      const content = await readFile(path.join(current.vault, "20-Knowledge", "current.md"), "utf8");
      return {ok: true, isError: false, content: {path: "20-Knowledge/current.md", content}};
    },
    async streamModelProxy(body, onEvent) {
      requests += 1;
      if (requests === 1) {
        assert.match(JSON.stringify(body.context.messages), /20-Knowledge\/current\.md/);
        emitTool(onEvent, "call-current", "get_current_note", {});
      } else {
        assert.match(JSON.stringify(body.context.messages), /一致性/);
        emitText(onEvent, "当前笔记包含一致性、无混杂与重叠性。");
      }
    },
  }));
  try {
    const chunks = await consume(runtime, {
      message: "只根据当前笔记概括识别假设。",
      conversationId: "repair-acceptance-b",
      activeNote: {path: "20-Knowledge/current.md"},
    });
    assert.equal(requests, 2);
    assert.deepEqual(
      chunks.filter(item => item.type === "tool_result").map(item => item.name),
      ["get_current_note"],
    );
  } finally {
    runtime.cleanup();
    await current.dispose();
  }
});

test("repair acceptance C: cross-Vault Q&A searches then reads a real temporary note", async () => {
  const current = await fixture();
  let requests = 0;
  const runtime = new current.PiAgentRuntime(baseTransport({
    async toolContracts() {
      return {
        schemaVersion: 1,
        items: [
          contract("search_vault", "Search the Vault"),
          contract("read_vault_note", "Read one Vault note"),
        ],
      };
    },
    async callRuntimeTool(body) {
      if (body.toolName === "search_vault") {
        const names = await readdir(path.join(current.vault, "20-Knowledge", "Concepts"));
        return {
          ok: true,
          isError: false,
          content: {items: names.map(name => ({path: `20-Knowledge/Concepts/${name}`}))},
        };
      }
      assert.equal(body.toolName, "read_vault_note");
      assert.equal(body.arguments.path, "20-Knowledge/Concepts/倾向得分.md");
      const content = await readFile(path.join(current.vault, body.arguments.path), "utf8");
      return {ok: true, isError: false, content: {path: body.arguments.path, content}};
    },
    async streamModelProxy(body, onEvent) {
      requests += 1;
      const context = JSON.stringify(body.context.messages);
      if (requests === 1) {
        emitTool(onEvent, "call-search", "search_vault", {query: "倾向得分"});
      } else if (requests === 2) {
        assert.match(context, /倾向得分\.md/);
        emitTool(onEvent, "call-read", "read_vault_note", {path: "20-Knowledge/Concepts/倾向得分.md"});
      } else {
        assert.match(context, /处理组与对照组/);
        emitText(onEvent, "跨库证据说明倾向得分用于平衡，并且仍需检查重叠性。");
      }
    },
  }));
  try {
    const chunks = await consume(runtime, {
      message: "搜索整个 Vault，读取相关正式笔记后回答。",
      conversationId: "repair-acceptance-c",
    });
    assert.equal(requests, 3);
    assert.deepEqual(
      chunks.filter(item => item.type === "tool_result").map(item => item.name),
      ["search_vault", "read_vault_note"],
    );
  } finally {
    runtime.cleanup();
    await current.dispose();
  }
});

test("repair acceptance O: study-note Pi turn applies a real temporary Draft Action", async () => {
  const current = await fixture();
  const targetRelative = "20-Knowledge/Drafts/学习笔记.md";
  const target = path.join(current.vault, targetRelative);
  await mkdir(path.dirname(target), {recursive: true});
  let requests = 0;
  const runtime = new current.PiAgentRuntime(baseTransport({
    async toolContracts() {
      return {
        schemaVersion: 1,
        items: [
          {...contract("plan_vault_change", "Plan a governed Draft write"), mutates_state: true, permission_level: "proposal"},
          {...contract("apply_vault_change", "Atomically apply a governed Draft write"), mutates_state: true, permission_level: "proposal"},
        ],
      };
    },
    async callRuntimeTool(body) {
      if (body.toolName === "plan_vault_change") {
        assert.equal(body.arguments.writes[0].path, targetRelative);
        return {
          ok: true,
          isError: false,
          content: {
            id: "plan-study-note",
            state: "planned",
            writes: body.arguments.writes,
            requires_confirmation: false,
          },
        };
      }
      assert.equal(body.toolName, "apply_vault_change");
      assert.equal(body.arguments.plan_id, "plan-study-note");
      const content = "# 学习笔记\n\n倾向得分用于协变量平衡；仍需检查重叠性。\n";
      const temporary = `${target}.tmp`;
      await writeFile(temporary, content, "utf8");
      await rename(temporary, target);
      return {
        ok: true,
        isError: false,
        content: {
          state: "applied",
          actionId: "action-study-note",
          undoAvailable: true,
          verification: {verified: true},
          files: [{path: targetRelative}],
        },
      };
    },
    async streamModelProxy(body, onEvent) {
      requests += 1;
      const context = JSON.stringify(body.context.messages);
      if (requests === 1) {
        assert.match(context, /study_note/);
        emitTool(onEvent, "call-plan", "plan_vault_change", {
          title: "生成学习笔记",
          writes: [{path: targetRelative, content: "# 学习笔记"}],
        });
      } else if (requests === 2) {
        assert.match(context, /plan-study-note/);
        emitTool(onEvent, "call-apply", "apply_vault_change", {plan_id: "plan-study-note"});
      } else {
        assert.match(context, /action-study-note/);
        emitText(onEvent, "学习笔记已写入可撤销草稿。");
      }
    },
  }));
  try {
    let finalRun;
    for await (const update of current.runPiAssistantTurn({
      runtime,
      message: "根据当前小节生成学习笔记。",
      conversationId: "repair-acceptance-o",
      context: {surface: "study_note", lessonVersion: "v3"},
      options: {allow_network: false, permission_mode: "ask"},
    })) {
      finalRun = update.run;
    }
    assert.equal(requests, 3);
    assert.match(await readFile(target, "utf8"), /仍需检查重叠性/);
    const applied = finalRun.toolCalls.find(item => item.tool === "apply_vault_change");
    assert.equal(applied.status, "completed");
    assert.equal(applied.result.result.actionId, "action-study-note");
    assert.equal(applied.result.result.verification.verified, true);
    assert.equal(finalRun.status, "completed");
  } finally {
    runtime.cleanup();
    await current.dispose();
  }
});
