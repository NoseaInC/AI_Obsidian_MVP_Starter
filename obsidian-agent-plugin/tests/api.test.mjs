import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {transform} from "esbuild";

test("API client only accepts localhost", () => {
  const source = readFileSync(new URL("../src/api.ts", import.meta.url), "utf8");
  assert.match(source, /127\.0\.0\.1/);
  assert.match(source, /Agent URL must be localhost/);
  assert.match(source, /protocol_version/);
  assert.match(source, /Agent health protocol mismatch/);
  assert.match(source, /requestUrl/);
  assert.match(source, /\/api\/v1/);
  assert.match(source, /Authorization/);
  assert.doesNotMatch(source, /API[_ -]?KEY/i);
});

test("plugin owns a tokenized localhost runtime lifecycle", () => {
  const source = readFileSync(new URL("../src/process-manager.ts", import.meta.url), "utf8");
  assert.match(source, /randomBytes\(32\)/);
  assert.match(source, /OBSIDIAN_AGENT_SESSION_TOKEN/);
  assert.match(source, /OBSIDIAN_AGENT_RUNTIME_ID/);
  assert.match(source, /existing\.vault !== this\.vaultPath/);
  assert.match(source, /process\.kill\(existing\.pid/);
  assert.match(source, /127\.0\.0\.1/);
  assert.match(source, /SIGTERM/);
  assert.match(source, /agent\.api\.server/);
  assert.doesNotMatch(source, /shell:\s*true/);
});

test("plugin exposes required commands and explicit confirmation", () => {
  const source = readFileSync(new URL("../main.ts", import.meta.url), "utf8");
  for (const id of ["import-pdf", "view-jobs", "review", "expand-idea", "today-learning", "next-week"]) assert.match(source, new RegExp(`id: "${id}"`));
  assert.match(source, /LearningAgentMainView/);
  assert.doesNotMatch(source, /LearningAgentSidebarView|id: "open-sidebar"|openSidebar\(/);
  assert.doesNotMatch(source, /window\.confirm|window\.prompt|window\.alert/);
});

test("PDF import creates a visible job and refreshes the materials surface", () => {
  const main = readFileSync(new URL("../main.ts", import.meta.url), "utf8");
  const views = readFileSync(new URL("../src/views.ts", import.meta.url), "utf8");
  assert.match(main, /PdfImportModal/);
  assert.match(main, /await this\.client\.post\("\/jobs"/);
  assert.match(main, /await this\.submitted\(\)/);
  assert.match(views, /filter === "failed"/);
  assert.match(views, /处理失败/);
  assert.match(views, /Change Set 概览/);
});

test("recommendation filtering, sorting and state counts are deterministic", async () => {
  const source = readFileSync(new URL("../src/recommendations.ts", import.meta.url), "utf8");
  const {code} = await transform(source, {loader: "ts", format: "esm", target: "es2020"});
  const mod = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);
  const items = [
    {id:"a",title:"主线复习",kind:"review",estimatedMinutes:8,score:70,reason:"到期",domain:"统计",route:"mainline",dueState:"overdue"},
    {id:"b",title:"支线学习",kind:"learn",estimatedMinutes:5,score:80,reason:"下一步",domain:"Agent",route:"branch",dueState:"upcoming"},
  ];
  assert.equal(mod.selectRecommendations(items, "主线", "all", "smart")[0].id, "a");
  assert.equal(mod.selectRecommendations(items, "", "all", "short")[0].id, "b");
  assert.equal(mod.selectRecommendations(items, "", "all", "mainline")[0].id, "a");
  assert.equal(mod.activeJobs([{state:"running"},{state:"failed"},{state:"awaiting_confirmation"}]).length, 2);
  assert.equal(mod.pendingBundles([{state:"prepared"},{state:"applied"}]).length, 1);
});

test("workspace contains chat-first shell and real interaction components", () => {
  const source = readFileSync(new URL("../src/views.ts", import.meta.url), "utf8");
  for (const component of ["la-workspace","la-module-nav","la-chat-first","la-material-center","la-review-layout","la-plan-focus","la-today-focus","la-unified-composer","la-artifact-card","la-action-bar"]) assert.match(source, new RegExp(component));
  for (const action of ["later","tomorrow","weekend","favorite","not_interested"]) assert.match(source, new RegExp(action));
  assert.match(source, /requires_confirmation/);
  assert.match(source, /\/intake\/submit/);
  assert.match(source, /uploadAttachment/);
  assert.doesNotMatch(source, /la-tabs/);
  assert.doesNotMatch(source, /window\.confirm|window\.prompt|window\.alert/);
});

test("retired right sidebar is detached and can no longer be opened", () => {
  const main = readFileSync(new URL("../main.ts", import.meta.url), "utf8");
  const views = readFileSync(new URL("../src/views.ts", import.meta.url), "utf8");
  assert.match(main, /onLayoutReady/);
  assert.match(main, /workspace\.on\("layout-change"/);
  assert.match(main, /LEGACY_SIDEBAR_VIEW/);
  assert.match(main, /removeRetiredSidebars/);
  assert.match(main, /detachLeavesOfType\(viewType\)/);
  assert.match(main, /addRibbonIcon\("brain-circuit", "知序", async \(\) => \{ await this\.openMain\("today"\); \}\)/);
  assert.doesNotMatch(main, /registerView\(SIDEBAR_VIEW|LearningAgentSidebarView|getRightLeaf|openSidebar\(|id: "open-sidebar"/);
  assert.doesNotMatch(views, /class LearningAgentSidebarView|la-sidebar|renderStatsInspector|inspectorOpen|la-stats-inspector/);
});

test("chat-first UI includes five modules, Artifact surfaces, provider drawer and strict scroll ownership", () => {
  const views = readFileSync(new URL("../src/views.ts", import.meta.url), "utf8");
  const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");
  for (const surface of ["la-material-center","la-review-layout","la-plan-focus","la-today-focus","la-assistant-shell-v3","la-provider-drawer","la-artifact-card"]) assert.match(views + css, new RegExp(surface));
  for (const field of ["Base URL","API Key","模型名称","任务模型路由","OpenAI-compatible","Custom"]) assert.match(views, new RegExp(field));
  for (const module of ["today", "sources", "review", "plan", "assistant"]) assert.match(views, new RegExp(`id: "${module}"`));
  assert.doesNotMatch(views, /学习 Agent/);
  assert.doesNotMatch(views, /renderGlobalHeader|la-global-header|全局快速搜索/);
  assert.doesNotMatch(views, /createDiv\(\{cls: "la-history-nav"\}\)/);
  assert.match(css, /button\.la-material-row[\s\S]*height: auto !important/);
  assert.match(css, /button\.la-review-row[\s\S]*height: auto !important/);
  assert.match(css, /data-type="learning-agent-main"[^\n]*> \.view-header[\s\S]*display: none !important/);
  assert.match(css, /grid-template-columns: 188px minmax\(0, 1fr\)/);
  assert.match(css, /\.la-module-content[\s\S]*overflow: hidden/);
  assert.match(css, /\.la-pane-scroll[\s\S]*overflow: hidden auto/);
  assert.match(css, /@container \(max-width: 1179px\)/);
  assert.match(css, /@container \(max-width: 859px\)/);
});

test("workspace chrome omits redundant global and native headers", () => {
  const views = readFileSync(new URL("../src/views.ts", import.meta.url), "utf8");
  const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");
  const preview = readFileSync(new URL("../visual-preview/index.html", import.meta.url), "utf8");
  assert.doesNotMatch(preview, /la-global-header|la-global-search|la-global-tools|快速搜索（⌘K）|id="module-name"/);
  assert.doesNotMatch(preview, /la-stats-inspector|今日统计|<button>统计<\/button>/);
  assert.doesNotMatch(css, /la-stats-inspector|la-inspector-toggle|la-summary-row|la-duration-summary/);
  assert.doesNotMatch(views + css + preview, /la-nav-bottom|la-nav-quick|la-nav-settings/);
  assert.doesNotMatch(views + preview, /输入问题或命令…/);
  assert.match(views + preview, /la-nav-runtime--footer/);
  assert.match(preview, /<section class="view-content la-app">\s*<div class="la-workspace">/);
  assert.match(preview, /scenario === "write-result"/);
  assert.match(preview, /scenario === "undo"/);
  assert.match(css, /workspace-leaf-content\[data-type="learning-agent-main"\] > \.view-header/);
});

test("review preview hides audit identifiers outside collapsed technical details", async () => {
  const source = readFileSync(new URL("../src/review-preview.ts", import.meta.url), "utf8");
  const {code} = await transform(source, {loader: "ts", format: "esm", target: "es2020"});
  const mod = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);
  const packet = [
    "# Review: pdf-secret:concept:0",
    "",
    "- Path: `/private/vault/note.md`",
    "- Source: `pdf-secret`",
    "",
    "## Content",
    "",
    "---",
    "artifact_id: pdf-secret:concept:0",
    "---",
    "# 倾向得分充分性",
    "",
    "人类可读正文。",
  ].join("\n");
  const preview = mod.humanReviewMarkdown(packet);
  assert.equal(preview, "# 倾向得分充分性\n\n人类可读正文。");
  assert.doesNotMatch(preview, /pdf-secret|private\/vault|artifact_id/);
});

test("assistant UI uses the Pydantic runtime and inline governed confirmation", () => {
  const views = readFileSync(new URL("../src/views.ts", import.meta.url), "utf8");
  const api = readFileSync(new URL("../src/api.ts", import.meta.url), "utf8");
  const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");
  for (const mode of ["对话", "整理", "研究", "规划"]) assert.match(views, new RegExp(mode));
  for (const component of ["la-chat-first", "la-brain-timeline", "la-brain-result", "la-change-set-proposal", "la-brain-error", "la-artifact-card"]) assert.match(views + css, new RegExp(component));
  assert.match(views, /PydanticAgentRuntime/);
  assert.match(views, /renderInlineAgentConfirmation/);
  assert.match(views, /\/change-sets\/.*\/diff/);
  assert.doesNotMatch(views, /assistant.*\/intake\/submit/);
  assert.doesNotMatch(views, /this\.client\.post<any>\("\/chat"/);
  assert.match(api, /Idempotency-Key/);
});

test("assistant model picker only changes chat routing", () => {
  const views = readFileSync(new URL("../src/views.ts", import.meta.url), "utf8");
  const handler = views.match(/selector\.onchange\s*=\s*\(\)\s*=>[^;]+;/)?.[0] ?? "";
  assert.match(handler, /assistant_chat/);
  assert.doesNotMatch(handler, /brain_orchestrator/);
});

test("provider settings expose Keychain references and all Brain model routes", () => {
  const views = readFileSync(new URL("../src/views.ts", import.meta.url), "utf8");
  for (const field of ["API Key Reference", "组织 ID（可选）", "支持流式响应", "支持 JSON Schema", "支持工具调用"]) assert.match(views, new RegExp(field));
  for (const route of ["brain_orchestrator", "intent_router", "curriculum_planner", "research_synthesis", "tutor", "quiz", "evaluation", "pdf_prepare", "assistant_chat"]) assert.match(views, new RegExp(route));
  assert.match(views, /apiKeyReference: keyReference\.value/);
  assert.match(views, /organizationId: organizationId\.value/);
  assert.doesNotMatch(views, /localStorage|sessionStorage/);
});

test("five-page integration includes research bundles, curriculum proposals and Brain quality gates", () => {
  const views = readFileSync(new URL("../src/views.ts", import.meta.url), "utf8");
  for (const value of ["研究包", "Research Bundle", "AI 补全", "周末计划", "主脑质量门", "Policy", "Verifier"]) assert.match(views, new RegExp(value));
  assert.match(views, /\/research-bundles/);
  assert.match(views, /\/curriculum\/refresh/);
});

test("Brain diagnostics commands are present and exported diagnostics stay local-only", () => {
  const main = readFileSync(new URL("../main.ts", import.meta.url), "utf8");
  for (const id of ["diagnostics", "brain-recent", "brain-retry-latest", "brain-cancel-current", "model-test", "curriculum-refresh", "export-diagnostics"]) assert.match(main, new RegExp(`id: "${id}"`));
  assert.match(main, /90-Local-Only\/AgentLogs/);
  assert.match(main, /\/brain\/diagnostics/);
  assert.doesNotMatch(main, /Authorization.*JSON\.stringify|apiKey.*write\(/i);
});

test("card controls align without negative icon offsets", () => {
  const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");
  assert.match(css, /\.la-rec-row__headline > strong[\s\S]*flex: 1 1 auto/);
  assert.match(css, /\.la-material-row__top,[\s\S]*align-items: center/);
  assert.doesNotMatch(css, /\.la-rec-row > \.la-icon-button \{[^}]*margin-(top|right):\s*-/s);
});
