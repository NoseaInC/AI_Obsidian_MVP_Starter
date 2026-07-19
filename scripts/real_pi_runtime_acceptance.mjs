#!/usr/bin/env node
/** Opt-in real-model acceptance for the production TypeScript Pi runtime.
 *
 * The companion Python launcher provides an ephemeral localhost Runtime and a
 * temporary Git-backed Vault.  This file never receives or resolves API keys.
 */

import assert from "node:assert/strict";
import {mkdtemp, readFile, rm} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {createRequire} from "node:module";

const baseUrl = String(process.env.ZHIXU_ACCEPTANCE_BASE_URL || "").replace(/\/$/, "");
const sessionToken = String(process.env.ZHIXU_ACCEPTANCE_SESSION_TOKEN || "");
const profileId = String(process.env.ZHIXU_ACCEPTANCE_PROFILE_ID || "");
const model = String(process.env.ZHIXU_ACCEPTANCE_MODEL || "");
const pluginRoot = String(process.env.ZHIXU_ACCEPTANCE_PLUGIN_ROOT || "");
const vaultRoot = String(process.env.ZHIXU_ACCEPTANCE_VAULT_ROOT || "");

if (!baseUrl || !sessionToken || !profileId || !model || !pluginRoot || !vaultRoot) {
  throw new Error("real_pi_acceptance_environment_incomplete");
}

const requireFromPlugin = createRequire(path.join(pluginRoot, "package.json"));
const esbuild = requireFromPlugin("esbuild");

async function loadRuntime() {
  const directory = await mkdtemp(path.join(os.tmpdir(), "zhixu-real-pi-"));
  const outfile = path.join(directory, "runtime.cjs");
  await esbuild.build({
    entryPoints: [path.join(pluginRoot, "src/core/runtime/PiAgentRuntime.ts")],
    outfile,
    bundle: true,
    format: "cjs",
    platform: "node",
    target: "node22",
    logLevel: "silent",
  });
  return {
    module: requireFromPlugin(outfile),
    dispose: () => rm(directory, {recursive: true, force: true}),
  };
}

async function request(method, route, body) {
  const response = await fetch(`${baseUrl}/api/v1${route}`, {
    method,
    headers: {
      Authorization: `Bearer ${sessionToken}`,
      ...(body === undefined ? {} : {"Content-Type": "application/json"}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload?.error?.message || `HTTP ${response.status}`);
  return payload;
}

const transport = {
  async registerTaskAuthorization(body) { return request("POST", "/task-authorizations", body); },
  async appendRuntimeEvents(body) { return request("POST", "/agent/events", body); },
  async runtimeEvents(runId, afterSequence = 0) {
    return request("GET", `/agent/runs/${encodeURIComponent(runId)}/events?after=${afterSequence}`);
  },
  async runtimeSession(sessionId) {
    return request("GET", `/agent/sessions/${encodeURIComponent(sessionId)}`);
  },
  async controlRuntimeRun(runId, type, text) {
    return request("POST", `/agent/runs/${encodeURIComponent(runId)}/control`, {type, text});
  },
  async cancelRuntimeRun(runId) {
    return request("POST", `/agent/runs/${encodeURIComponent(runId)}/cancel`, {});
  },
  async toolContracts() { return request("GET", "/tools/contracts"); },
  async callRuntimeTool(body) {
    const response = await request("POST", "/tools/call", body);
    if (body?.toolName !== "activate_runtime_upgrade" || response?.ok !== true) return response;
    const healthResponse = await fetch(`${baseUrl}/health`);
    const health = await healthResponse.json();
    assert.equal(healthResponse.status, 200);
    assert.equal(health.ok, true);
    return {
      ...response,
      content: {
        ...response.content,
        deployment: {
          activationProtocolObserved: true,
          installed: true,
          restarted: true,
          healthy: true,
        },
      },
    };
  },
  async streamModelProxy(body, onEvent, signal) {
    const response = await fetch(`${baseUrl}/api/v1/model/stream`, {
      method: "POST",
      headers: {Authorization: `Bearer ${sessionToken}`, "Content-Type": "application/json"},
      body: JSON.stringify(body),
      signal,
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload?.error?.message || `HTTP ${response.status}`);
    }
    assert.ok(response.body, "model proxy must return an NDJSON stream");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) if (line.trim()) onEvent(JSON.parse(line));
    }
    buffer += decoder.decode();
    if (buffer.trim()) onEvent(JSON.parse(buffer));
  },
};

function tools(chunks) {
  return chunks.filter(item => item.type === "tool_result" && item.status === "completed").map(item => item.name);
}

function answer(chunks) {
  return chunks.filter(item => item.type === "text").map(item => item.content).join("");
}

function toolResult(chunks, name) {
  const chunk = [...chunks].reverse().find(item => item.type === "tool_result" && item.name === name && item.status === "completed");
  return chunk?.result?.result || {};
}

function assertCompleted(label, chunks) {
  const errors = chunks.filter(item => item.type === "error");
  assert.deepEqual(errors, [], `${label} emitted runtime errors: ${JSON.stringify(errors)}`);
  assert.equal(chunks.at(-1)?.type, "done", `${label} did not terminate`);
  assert.equal(chunks.at(-1)?.status, "completed", `${label} did not complete`);
}

async function runTurn(runtime, label, requestBody, controls) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 240_000);
  const chunks = [];
  let controlled = false;
  try {
    const turn = runtime.prepareTurn({
      conversationId: requestBody.conversationId || `acceptance-${label.toLowerCase()}`,
      profileId,
      model,
      message: requestBody.message,
      activeNote: requestBody.activeNote,
      options: {reasoning_mode: "deep", networkAuthorized: requestBody.networkAuthorized === true},
    });
    const runId = turn.payload.identity.runId;
    for await (const chunk of runtime.query(turn, controller.signal)) {
      chunks.push(chunk);
      if (!controlled && controls && (chunk.type === "tool_result" || chunk.type === "text")) {
        controlled = true;
        await controls(runtime, runId);
      }
    }
    assertCompleted(label, chunks);
    return chunks;
  } finally {
    clearTimeout(timeout);
  }
}

const {module, dispose} = await loadRuntime();
const runtime = new module.PiAgentRuntime(transport);
const report = {schemaVersion: 1, model, temporaryVault: true, cases: {}};

try {
  const caseA = await runTurn(runtime, "A", {
    message: "必须调用 get_current_note 读取当前笔记，然后只根据实际正文说明它提出的三条识别假设。",
    activeNote: {path: "20-Knowledge/Drafts/当前论证.md"},
  });
  assert.ok(tools(caseA).includes("get_current_note"));
  assert.match(answer(caseA), /一致性|无混杂|重叠/);
  report.cases.A = {ok: true, tools: tools(caseA)};

  const caseB = await runTurn(runtime, "B", {
    conversationId: "acceptance-b-focus",
    message: "这是工具链验收：先调用 get_conversation_focus；拿到 Focus 后必须调用名为 search_vault 的工具搜索 Vault（get_vault_overview 不能替代）；再从搜索结果调用 read_vault_note 读取至少一篇正式笔记，最后说明当前方法与倾向得分的关系。不得凭记忆或直接猜路径跳过任何一步。",
  });
  assert.ok(tools(caseB).includes("get_conversation_focus"), `B tools: ${tools(caseB).join(",")}`);
  assert.ok(tools(caseB).some(name => ["search_vault", "get_vault_overview", "list_vault_folder"].includes(name)), `B tools: ${tools(caseB).join(",")}`);
  assert.ok(tools(caseB).some(name => ["read_vault_note", "read_note_excerpt"].includes(name)), `B tools: ${tools(caseB).join(",")}`);
  report.cases.B = {ok: true, tools: tools(caseB)};

  const beforeC = await readFile(path.join(vaultRoot, "20-Knowledge/Drafts/当前论证.md"), "utf8");
  const caseC = await runTurn(runtime, "C", {
    message: "读取当前笔记，在末尾新增一个‘诊断清单’小节，必须依次使用 plan_vault_change 和 apply_vault_change 直接完成可逆写入并校验；不要等待二次确认。",
    activeNote: {path: "20-Knowledge/Drafts/当前论证.md"},
  });
  assert.ok(tools(caseC).some(name => ["get_current_note", "read_vault_note", "read_note_excerpt"].includes(name)), `C tools: ${tools(caseC).join(",")}`);
  assert.ok(tools(caseC).includes("plan_vault_change"), `C tools: ${tools(caseC).join(",")}`);
  assert.ok(tools(caseC).includes("apply_vault_change"), `C tools: ${tools(caseC).join(",")}`);
  const actionC = toolResult(caseC, "apply_vault_change");
  assert.equal(actionC.verification?.verified, true);
  assert.ok(actionC.actionId);
  const afterC = await readFile(path.join(vaultRoot, "20-Knowledge/Drafts/当前论证.md"), "utf8");
  assert.notEqual(afterC, beforeC);
  assert.match(afterC, /诊断清单/);
  report.cases.C = {ok: true, tools: tools(caseC), verified: true};

  const caseD = await runTurn(runtime, "D", {
    message: `使用 undo_agent_action 撤销 action_id ${actionC.actionId}，并报告真实撤销结果。`,
  });
  assert.ok(tools(caseD).includes("undo_agent_action"));
  const restored = await readFile(path.join(vaultRoot, "20-Knowledge/Drafts/当前论证.md"), "utf8");
  assert.equal(restored, beforeC);
  report.cases.D = {ok: true, tools: tools(caseD), exactBytesRestored: true};

  const caseE = await runTurn(runtime, "E", {
    message: "读取当前笔记并为增加‘敏感性分析’小节生成 plan_vault_change，但这是仅规划任务：绝对不要调用 apply_vault_change，也不要改变文件。",
    activeNote: {path: "20-Knowledge/Drafts/当前论证.md"},
  });
  assert.ok(tools(caseE).includes("plan_vault_change"));
  assert.ok(!tools(caseE).includes("apply_vault_change"));
  assert.equal(await readFile(path.join(vaultRoot, "20-Knowledge/Drafts/当前论证.md"), "utf8"), beforeC);
  report.cases.E = {ok: true, tools: tools(caseE), unchanged: true};

  const caseF = await runTurn(runtime, "F", {
    message: "这是临时验收仓库中的完整开发部署任务。创建隔离 Git worktree，读取 package.json，把 version 改为 1.0.1，运行 npm test 和 npm run build，查看 diff，提交并合并任务分支；最后必须调用 activate_runtime_upgrade 完成安装、Runtime 重启与健康检查。所有操作必须使用受控开发工具，不要联网。",
  });
  const requiredF = ["create_git_worktree", "read_workspace_file", "write_workspace_file", "run_command", "git_diff", "git_commit", "merge_task_branch", "activate_runtime_upgrade"];
  for (const name of requiredF) assert.ok(tools(caseF).includes(name), `F missing ${name}`);
  const packageJson = JSON.parse(await readFile(path.join(vaultRoot, "package.json"), "utf8"));
  assert.equal(packageJson.version, "1.0.1");
  const activationF = toolResult(caseF, "activate_runtime_upgrade");
  assert.equal(activationF.deployment?.installed, true);
  assert.equal(activationF.deployment?.restarted, true);
  assert.equal(activationF.deployment?.healthy, true);
  report.cases.F = {ok: true, tools: tools(caseF), merged: true, testsAndBuild: true, activationProtocol: true};

  const caseG = await runTurn(runtime, "G", {
    conversationId: "acceptance-g-steering",
    message: "搜索 Vault 并读取关于倾向得分的笔记，然后给出三点摘要。",
  }, async (currentRuntime, runId) => {
    await currentRuntime.steer(runId, "把最终答案调整为两点，并强调重叠性诊断。");
  });
  assert.match(answer(caseG), /重叠/);
  report.cases.G = {ok: true, tools: tools(caseG), steeringAccepted: true};

  const caseH = await runTurn(runtime, "H", {
    conversationId: "acceptance-h-follow-up",
    message: "读取当前笔记并概括核心结论。",
    activeNote: {path: "20-Knowledge/Drafts/当前论证.md"},
  }, async (currentRuntime, runId) => {
    await currentRuntime.followUp(runId, "继续回答：最重要的假设是什么？");
    await currentRuntime.followUp(runId, "继续回答：最常见的诊断是什么？");
    await currentRuntime.followUp(runId, "继续回答：最大的局限是什么？");
  });
  assert.ok(tools(caseH).includes("get_current_note"));
  report.cases.H = {ok: true, tools: tools(caseH), queuedFollowUps: 3};

  process.stdout.write(`${JSON.stringify(report)}\n`);
} finally {
  runtime.cleanup();
  await dispose();
}
