import {App, FileSystemAdapter, Menu, Modal, Notice, Plugin, PluginSettingTab, Setting} from "obsidian";
import {AgentClient} from "./src/api";
import {AgentProcessManager, RuntimeSettings} from "./src/process-manager";
import {LearningAgentMainView, LEGACY_SIDEBAR_VIEW, MAIN_VIEW, SIDEBAR_VIEW} from "./src/views";

type MainTab = "today" | "sources" | "review" | "plan" | "assistant";
interface LearningAgentSettings extends RuntimeSettings {
  behaviorPersonalization: boolean;
  recordLearningDuration: boolean;
  useQuizResults: boolean;
  useRecommendationFeedback: boolean;
  useAssistantSummaries: boolean;
  useRecentMaterials: boolean;
  dailyKnowledgeCount: number;
  trustedResearch: boolean;
  openWebResearch: boolean;
  behaviorTrackingPaused: boolean;
}
const DEFAULT_SETTINGS: LearningAgentSettings = {
  port: 8765, pythonPath: "", autoStart: true, stopOnUnload: true,
  behaviorPersonalization: true, recordLearningDuration: true, useQuizResults: true,
  useRecommendationFeedback: true, useAssistantSummaries: false, useRecentMaterials: true,
  dailyKnowledgeCount: 1, trustedResearch: true, openWebResearch: false, behaviorTrackingPaused: false,
};

class LearningDataConfirmModal extends Modal {
  constructor(app: App, private titleText: string, private description: string, private confirmed: () => Promise<void>) { super(app); }
  onOpen() {
    this.contentEl.addClass("la-modal"); this.contentEl.createEl("h2", {text: this.titleText}); this.contentEl.createEl("p", {text: this.description});
    const actions = this.contentEl.createDiv({cls: "la-modal__actions"});
    actions.createEl("button", {text: "取消"}).onclick = () => this.close();
    const confirm = actions.createEl("button", {text: "确认清除", cls: "mod-warning"});
    confirm.onclick = async () => { confirm.disabled = true; try { await this.confirmed(); this.close(); } catch (error: any) { new Notice(`清除失败：${error.message}`); confirm.disabled = false; } };
  }
}

class PdfImportModal extends Modal {
  private pdf = ""; private kind: "paper" | "textbook"; private domainFocus = ""; private model = "deepseek-v4-pro"; private maxConcepts = 3;
  constructor(app: App, private client: AgentClient, kind: "paper" | "textbook", private submitted: () => Promise<void>) { super(app); this.kind = kind; }
  onOpen() {
    this.contentEl.addClass("la-modal"); this.contentEl.createEl("h2", {text: this.kind === "paper" ? "导入论文 PDF" : "导入教材 PDF"});
    this.contentEl.createEl("p", {text: "PDF 只在本机读取；仅带 PAGE 标记的提取文本会发送给已配置的模型。", cls: "la-muted"});
    new Setting(this.contentEl).setName("本地 PDF 路径").setDesc("绝对路径，不会复制 PDF 到 Vault")
      .addText(text => text.setPlaceholder("/Users/…/paper.pdf").onChange(value => this.pdf = value.trim()));
    new Setting(this.contentEl).setName("资料类型").addDropdown(dropdown => dropdown.addOption("paper", "论文").addOption("textbook", "教材").setValue(this.kind).onChange(value => this.kind = value as "paper" | "textbook"));
    new Setting(this.contentEl).setName("领域提示").setDesc("可选，例如：因果推断").addText(text => text.onChange(value => this.domainFocus = value.trim()));
    new Setting(this.contentEl).setName("模型").addText(text => text.setValue(this.model).onChange(value => this.model = value.trim()));
    new Setting(this.contentEl).setName("概念上限").setDesc("最多创建 0–3 篇高价值概念草稿").addSlider(slider => slider.setLimits(0, 3, 1).setValue(this.maxConcepts).setDynamicTooltip().onChange(value => this.maxConcepts = value));
    const actions = this.contentEl.createDiv({cls: "la-modal__actions"}); actions.createEl("button", {text: "取消"}).onclick = () => this.close();
    const submit = actions.createEl("button", {text: "开始处理", cls: "mod-cta"}); submit.onclick = async () => {
      if (!this.pdf) { new Notice("请填写本地 PDF 路径"); return; } submit.disabled = true;
      try { await this.client.post("/jobs", {kind: "prepare-pdf", payload: {pdf: this.pdf, kind: this.kind, domain_focus: this.domainFocus, model: this.model, max_concepts: this.maxConcepts}}); this.close(); new Notice("资料已加入处理队列"); await this.submitted(); }
      catch (error: any) { new Notice(`导入失败：${error.message}`); submit.disabled = false; }
    };
  }
}

class AgentSettingsTab extends PluginSettingTab {
  constructor(app: App, private plugin: LearningAgentPlugin) { super(app, plugin); }
  display() {
    const {containerEl} = this; containerEl.empty(); containerEl.createEl("h2", {text: "知序"}); const manager = this.plugin.manager;
    new Setting(containerEl).setName("运行状态").setDesc(manager ? `${manager.status}${manager.pid ? ` · PID ${manager.pid}` : ""}${manager.lastError ? ` · ${manager.lastError}` : ""}` : "不可用")
      .addButton(control => control.setButtonText("重启 Agent").onClick(async () => { await manager?.restart(); this.display(); }));
    new Setting(containerEl).setName("自动启动后台 Agent").setDesc("打开 Obsidian 时启动受限本地 Runtime").addToggle(control => control.setValue(this.plugin.settings.autoStart).onChange(async value => { this.plugin.settings.autoStart = value; await this.plugin.saveSettings(); }));
    new Setting(containerEl).setName("插件关闭时停止服务").addToggle(control => control.setValue(this.plugin.settings.stopOnUnload).onChange(async value => { this.plugin.settings.stopOnUnload = value; await this.plugin.saveSettings(); }));
    new Setting(containerEl).setName("Python 命令").setDesc(`留空时使用项目虚拟环境。当前：${manager?.pythonPath ?? "不可用"}`).addText(control => control.setPlaceholder("自动").setValue(this.plugin.settings.pythonPath).onChange(async value => { this.plugin.settings.pythonPath = value.trim(); await this.plugin.saveSettings(); }));
    new Setting(containerEl).setName("本地端口").setDesc("仅监听 127.0.0.1").addText(control => control.setValue(String(this.plugin.settings.port)).onChange(async value => { const port = Number(value); if (Number.isInteger(port) && port >= 1024 && port <= 65535) { this.plugin.settings.port = port; await this.plugin.saveSettings(); } }));
    new Setting(containerEl).setName("模型 Provider 与 API").setDesc("支持 DeepSeek、OpenAI、OpenAI-compatible 和 Custom；API Key 仅进入 macOS Keychain。")
      .addButton(control=>control.setButtonText("打开模型设置").onClick(async()=>{await this.plugin.openMain("assistant");}));
    new Setting(containerEl).setName("Vault 自治权限").setDesc("查看可读写范围、保护边界和自治等级；快照、审计与 Undo 始终启用。")
      .addButton(control => control.setButtonText("查看权限").onClick(() => new AutonomyPermissionModal(this.app, this.plugin.client).open()));
    containerEl.createEl("h3", {text: "学习行为与推荐"});
    new Setting(containerEl).setName("启用行为个性化").setDesc("仅记录结构化学习事件，不记录按键、完整笔记、完整对话或 Prompt。")
      .addToggle(control => control.setValue(this.plugin.settings.behaviorPersonalization).onChange(async value => { this.plugin.settings.behaviorPersonalization = value; await this.plugin.saveSettings(); }));
    new Setting(containerEl).setName("暂停行为记录").setDesc("暂停后基础复习、资料任务和本地排序仍可使用。")
      .addToggle(control => control.setValue(this.plugin.settings.behaviorTrackingPaused).onChange(async value => { this.plugin.settings.behaviorTrackingPaused = value; await this.plugin.saveSettings(); }));
    for (const [name, description, key] of [
      ["记录学习时长", "用于判断工作日与周末的时间适配。", "recordLearningDuration"],
      ["使用小测结果", "只使用正确率和错误标签，不保存完整作答正文。", "useQuizResults"],
      ["使用推荐反馈", "稍后、太难、不感兴趣等反馈会影响冷却和排序。", "useRecommendationFeedback"],
      ["使用助手对话摘要", "默认关闭；不会发送或保存完整私人对话。", "useAssistantSummaries"],
      ["使用近期资料", "允许课程候选参考已导入资料的标题与结构化摘要。", "useRecentMaterials"],
      ["允许可信来源检索", "仅使用受控学术与官方来源 Provider。", "trustedResearch"],
      ["允许开放网页", "默认关闭；开启后仍执行 SSRF、大小和 Content-Type 检查。", "openWebResearch"],
    ] as Array<[string, string, keyof LearningAgentSettings]>) {
      new Setting(containerEl).setName(name).setDesc(description).addToggle(control => control.setValue(Boolean(this.plugin.settings[key])).onChange(async value => { (this.plugin.settings as any)[key] = value; await this.plugin.saveSettings(); }));
    }
    new Setting(containerEl).setName("每日新知识数量").setDesc("工作日最多 1 个；周末最多 2 个。")
      .addDropdown(control => control.addOption("0", "关闭").addOption("1", "1 个").addOption("2", "2 个（仅周末）").setValue(String(this.plugin.settings.dailyKnowledgeCount)).onChange(async value => { this.plugin.settings.dailyKnowledgeCount = Number(value); await this.plugin.saveSettings(); }));
    new Setting(containerEl).setName("查看学习画像与诊断").setDesc("显示运行时边界、事件数量、候选等级和模型配置，不显示秘密或私人正文。")
      .addButton(control => control.setButtonText("打开诊断").onClick(() => new DailyDiagnosticsModal(this.app, this.plugin.client).open()));
    new Setting(containerEl).setName("导出学习事件").setDesc("导出脱敏结构化事件到 90-Local-Only。")
      .addButton(control => control.setButtonText("导出").onClick(async () => {
        const data = await this.plugin.client.get<any>("/learning/events?limit=5000"); const folder = "90-Local-Only/AgentLogs";
        if (!await this.app.vault.adapter.exists(folder)) await this.app.vault.adapter.mkdir(folder);
        const path = `${folder}/learning-events-${new Date().toISOString().replace(/[:.]/g, "-")}.json`; await this.app.vault.adapter.write(path, JSON.stringify(data, null, 2)); new Notice(`已导出：${path}`);
      }));
    new Setting(containerEl).setName("清除学习行为").setDesc("只删除运行状态中的结构化事件和推断画像，不删除知识笔记。")
      .addButton(control => control.setButtonText("最近 7 天").onClick(() => new LearningDataConfirmModal(this.app, "清除最近 7 天行为", "不会删除任何 Markdown、资料或正式知识。", async () => { await this.plugin.client.delete("/learning/events?scope=recent-7-days"); new Notice("最近 7 天学习行为已清除"); }).open()))
      .addButton(control => control.setButtonText("全部画像").setWarning().onClick(() => new LearningDataConfirmModal(this.app, "清除全部学习画像", "这会删除结构化学习事件和推断画像，但不会删除 Markdown、资料或正式知识。", async () => { await this.plugin.client.delete("/learning/events?scope=all"); new Notice("学习画像已清除"); }).open()));
    new Setting(containerEl).setName("清除本地对话").setDesc("单会话可在助手历史菜单中导出或仅保留摘要；批量清除必须明确确认。")
      .addButton(control => control.setButtonText("最近 7 天").setWarning().onClick(() => new LearningDataConfirmModal(this.app, "清除最近 7 天对话", "会删除最近 7 天创建的原始对话及本地附件副本；已审核知识笔记不受影响。", async () => { await this.plugin.client.delete("/conversations?scope=recent-7-days&confirm=true"); new Notice("最近 7 天对话已清除"); }).open()))
      .addButton(control => control.setButtonText("全部对话").setWarning().onClick(() => new LearningDataConfirmModal(this.app, "清除全部对话", "会删除全部本地会话、附件副本和会话运行成果；已审核知识笔记不受影响。", async () => { await this.plugin.client.delete("/conversations?scope=all&confirm=true"); new Notice("全部本地对话已清除"); }).open()));
    containerEl.createEl("p", {text: "Session token 只存在内存和子进程环境；日志位于 90-Local-Only/AgentLogs。", cls: "la-muted"});
  }
}

class BrainDiagnosticsModal extends Modal {
  constructor(app: App, private client: AgentClient) { super(app); }
  async onOpen() {
    this.contentEl.addClass("la-modal", "la-diagnostics-modal");
    this.contentEl.createEl("h2", {text: "知序主脑诊断"});
    const content = this.contentEl.createDiv({cls: "la-diagnostics-content"});
    content.createEl("p", {text: "正在读取脱敏诊断…", cls: "la-muted"});
    try {
      const [data, actionResult, autonomy] = await Promise.all([
        this.client.get<any>("/brain/diagnostics"), this.client.get<any>("/agent-actions?limit=8"), this.client.get<any>("/autonomy"),
      ]); content.empty();
      for (const [title, facts] of [["服务", data.service], ["学习主脑", data.indexes], ["运行边界", data.runtime]] as Array<[string, Record<string, unknown>]>) {
        const section = content.createEl("section"); section.createEl("h3", {text: title});
        for (const [key, value] of Object.entries(facts ?? {})) {
          const row = section.createDiv({cls: "la-diagnostic-row"}); row.createSpan({text: key}); row.createEl("strong", {text: typeof value === "object" ? JSON.stringify(value) : String(value ?? "—")});
        }
      }
      const models = content.createEl("section"); models.createEl("h3", {text: "模型"});
      if (!data.models?.length) models.createEl("p", {text: "尚未建立模型 Profile；本地确定性能力仍可使用。", cls: "la-muted"});
      for (const model of data.models ?? []) models.createEl("p", {text: `${model.name} · ${model.provider} · ${model.base_url_host} · ${model.configured ? "Key 已配置" : "Key 未配置"} · ${model.model || "未选择模型"}`});
      const runs = content.createEl("section"); runs.createEl("h3", {text: "最近执行"});
      if (!data.brain_runs?.length) runs.createEl("p", {text: "暂无 Brain Run", cls: "la-muted"});
      for (const run of data.brain_runs ?? []) runs.createEl("p", {text: `${run.primary_intent || "待识别"} · ${run.status} · ${run.error_code || "正常"}`});
      const permissions = content.createEl("section"); permissions.createEl("h3", {text: `Vault 权限 · ${autonomy.mode}`});
      permissions.createEl("p", {text: `可以：${(autonomy.summary?.can ?? []).join("、")}`});
      permissions.createEl("p", {text: `不会：${(autonomy.summary?.willNot ?? []).join("、")}`, cls: "la-muted"});
      const actions = content.createEl("section"); actions.createEl("h3", {text: "最近 Agent 修改"});
      if (!(actionResult.actions ?? []).length) actions.createEl("p", {text: "暂无自动文件修改", cls: "la-muted"});
      for (const action of actionResult.actions ?? []) {
        const row = actions.createDiv({cls: "la-diagnostic-row"}); row.createSpan({text: `${action.action_type} · ${action.target} · ${action.status}`});
        const controls = row.createSpan();
        const view = controls.createEl("button", {text: "查看变化"}); view.onclick = async () => {
          const detail = await this.client.get<any>(`/agent-actions/${encodeURIComponent(action.id)}`);
          const modal = new Modal(this.app); modal.contentEl.addClass("la-modal"); modal.contentEl.createEl("h2", {text: "Agent 文件变化"});
          modal.contentEl.createEl("pre", {text: JSON.stringify({action: detail.action.details, changes: detail.action.changes, snapshots: detail.action.snapshots}, null, 2)}); modal.open();
        };
        if (action.status === "applied" && action.action_type !== "undo") {
          const undo = controls.createEl("button", {text: "撤销"}); undo.onclick = async () => { undo.disabled = true; try { await this.client.post(`/agent-actions/${encodeURIComponent(action.id)}/undo`, {}); new Notice("最近修改已撤销"); this.close(); new BrainDiagnosticsModal(this.app, this.client).open(); } catch (error: any) { new Notice(`无法撤销：${error.message}`); undo.disabled = false; } };
        }
      }
      if (data.recent_error) content.createEl("p", {text: `最近错误：${data.recent_error.error_code || "unknown"} · ${data.recent_error.updated_at || ""}`, cls: "la-muted"});
      const technical = content.createEl("details"); technical.createEl("summary", {text: "脱敏 JSON"}); technical.createEl("pre", {text: JSON.stringify(data, null, 2)});
    } catch (error: any) {
      content.empty(); content.createEl("p", {text: `诊断加载失败：${error.message}`});
    }
  }
}

class DailyDiagnosticsModal extends Modal {
  constructor(app: App, private client: AgentClient) { super(app); }
  async onOpen() {
    this.contentEl.addClass("la-modal", "la-diagnostics-modal"); this.contentEl.createEl("h2", {text: "每日智能诊断"});
    const content = this.contentEl.createDiv({cls: "la-diagnostics-content"}); content.createEl("p", {text: "正在读取脱敏状态…", cls: "la-muted"});
    try {
      const [daily, candidates, profile] = await Promise.all([this.client.get<any>("/daily/dashboard"), this.client.get<any>("/curriculum/candidates"), this.client.get<any>("/learning/profile")]);
      content.empty();
      const facts: Array<[string, unknown]> = [
        ["UI 与排序", daily.runtime?.ranking], ["持久化 Worker", daily.runtime?.persistence], ["Provider", daily.runtime?.provider ?? "未配置"],
        ["Key configured", daily.runtime?.modelConfigured ? "是" : "否"], ["行为事件", profile.profile?.eventCount ?? 0], ["画像覆盖天数", profile.profile?.coveredDays ?? 0],
        ["行为权重", `${Math.round((profile.profile?.behaviorWeight ?? 0) * 100)}%`], ["课程候选", candidates.items?.length ?? 0],
      ];
      const summary = content.createEl("section"); summary.createEl("h3", {text: "运行时边界"});
      for (const [label, value] of facts) { const row = summary.createDiv({cls: "la-diagnostic-row"}); row.createSpan({text: label}); row.createEl("strong", {text: String(value ?? "—")}); }
      const grades = content.createEl("section"); grades.createEl("h3", {text: "候选质量准入"});
      for (const grade of ["A", "B", "C"]) grades.createEl("p", {text: `${grade} 级 · ${(candidates.items ?? []).filter((item: any) => item.verification?.grade === grade).length}`});
      content.createEl("p", {text: "诊断不包含 API Key、Token、绝对路径、完整笔记、完整对话或完整 Prompt。", cls: "la-muted"});
    } catch (error: any) { content.empty(); content.createEl("p", {text: `诊断加载失败：${error.message}`}); }
  }
}

class ModelConnectionModal extends Modal {
  constructor(app: App, private client: AgentClient) { super(app); }
  async onOpen() {
    this.contentEl.addClass("la-modal"); this.contentEl.createEl("h2", {text: "测试模型连接"});
    const {profiles} = await this.client.get<any>("/model-profiles");
    const select = this.contentEl.createEl("select", {attr: {"aria-label": "选择模型 Profile"}});
    select.createEl("option", {value: "", text: "请选择模型 Profile"});
    for (const profile of profiles) select.createEl("option", {value: profile.id, text: `${profile.displayName} · ${profile.configured ? "Key 已配置" : "Key 未配置"}`});
    const result = this.contentEl.createEl("p", {text: "只调用最小连接检查，不发送知识正文。", cls: "la-muted"});
    const actions = this.contentEl.createDiv({cls: "la-modal__actions"});
    actions.createEl("button", {text: "关闭"}).onclick = () => this.close();
    const test = actions.createEl("button", {text: "测试连接", cls: "mod-cta"});
    test.onclick = async () => {
      if (!select.value) { result.setText("请先选择 Profile"); return; }
      test.disabled = true;
      try { const response = await this.client.post<any>(`/model-profiles/${select.value}/test`, {}); result.setText(`${response.message}（${response.code}）`); }
      catch (error: any) { result.setText(`连接失败：${error.message}`); }
      finally { test.disabled = false; }
    };
  }
}

class AutonomyPermissionModal extends Modal {
  constructor(app: App, private client: AgentClient) { super(app); }
  async onOpen() {
    this.contentEl.addClass("la-modal"); this.contentEl.createEl("h2", {text: "知序的 Vault 权限"});
    const content = this.contentEl.createDiv(); content.createEl("p", {text: "正在读取当前权限…", cls: "la-muted"});
    try {
      const status = await this.client.get<any>("/autonomy"); content.empty();
      content.createEl("p", {text: "所有自动修改都会先创建快照、记录变化，并仅在文件未再次变化时允许撤销。"});
      const can = content.createEl("section"); can.createEl("h3", {text: "Agent 可以"});
      const canList = can.createEl("ul"); for (const value of status.summary?.can ?? []) canList.createEl("li", {text: String(value)});
      const willNot = content.createEl("section"); willNot.createEl("h3", {text: "Agent 不会"});
      const noList = willNot.createEl("ul"); for (const value of status.summary?.willNot ?? []) noList.createEl("li", {text: String(value)});
      let mode = String(status.mode || "high");
      new Setting(content).setName("自治等级").setDesc("可随时降级；protected/core 和高风险操作始终需要确认。")
        .addDropdown(control => control.addOption("cautious", "谨慎").addOption("balanced", "平衡").addOption("high", "高自治").setValue(mode).onChange(value => mode = value));
      const actions = content.createDiv({cls: "la-modal__actions"});
      actions.createEl("button", {text: "稍后"}).onclick = () => this.close();
      const acknowledge = actions.createEl("button", {text: status.permissionSummaryAcknowledged ? "保存" : "了解并启用", cls: "mod-cta"});
      acknowledge.onclick = async () => { acknowledge.disabled = true; await this.client.patch("/autonomy", {mode, acknowledge_summary: true}); new Notice("Vault 权限设置已保存"); this.close(); };
    } catch (error: any) { content.empty(); content.createEl("p", {text: `权限状态加载失败：${error.message}`}); }
  }
}

export default class LearningAgentPlugin extends Plugin {
  client = new AgentClient(); settings: LearningAgentSettings = {...DEFAULT_SETTINGS}; manager: AgentProcessManager | null = null;
  async onload() {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData()); const adapter = this.app.vault.adapter;
    if (adapter instanceof FileSystemAdapter) { this.manager = new AgentProcessManager(adapter.getBasePath(), this.settings, () => {}); this.client.configure(this.manager.baseUrl, this.manager.sessionToken); void this.manager.ensureRunning().then(async () => { for (const leaf of this.app.workspace.getLeavesOfType(MAIN_VIEW)) if (leaf.view instanceof LearningAgentMainView) await leaf.view.refresh(); const autonomy = await this.client.get<any>("/autonomy"); if (!autonomy.permissionSummaryAcknowledged) new AutonomyPermissionModal(this.app, this.client).open(); }).catch(error => new Notice(`Agent 启动失败：${error.message}`)); }
    else new Notice("知序 仅支持本地桌面 Vault");
    this.addSettingTab(new AgentSettingsTab(this.app, this));
    this.registerView(MAIN_VIEW, leaf => new LearningAgentMainView(leaf, this.client, () => ({
      trackingEnabled: this.settings.behaviorPersonalization && !this.settings.behaviorTrackingPaused,
      recordLearningDuration: this.settings.recordLearningDuration,
      useQuizResults: this.settings.useQuizResults,
      useRecommendationFeedback: this.settings.useRecommendationFeedback,
      dailyKnowledgeCount: this.settings.dailyKnowledgeCount,
    })));
    this.removeRetiredSidebars();
    this.app.workspace.onLayoutReady(() => this.removeRetiredSidebars());
    this.registerEvent(this.app.workspace.on("layout-change", () => this.removeRetiredSidebars()));
    this.addCommand({id: "dashboard", name: "Agent: 打开首页", callback: () => this.openMain("today")});
    this.addCommand({id: "import-pdf", name: "Agent: 导入 PDF", callback: async () => { await this.openMain("assistant"); new Notice("请把 PDF 拖入助手或点击输入框下方的附件按钮"); }});
    this.addCommand({id: "import-textbook", name: "Agent: 导入教材", callback: async () => { await this.openMain("assistant"); new Notice("请把教材 PDF 拖入助手，并说明希望整理的章节或学习目标"); }});
    this.addCommand({id: "import-conversation", name: "Agent: 导入 AI 对话", callback: async () => { await this.openMain("assistant"); new Notice("请把对话文件或文本交给助手；原文只保存在本地私有区"); }});
    this.addCommand({id: "view-jobs", name: "Agent: 查看当前任务", callback: () => this.openMain("sources")});
    this.addCommand({id: "view-prepared", name: "Agent: 查看待确认资料", callback: () => this.openMain("sources")});
    this.addCommand({id: "review", name: "Agent: 查看待审核知识", callback: () => this.openMain("review")});
    this.addCommand({id: "today-learning", name: "Agent: 开始今日学习", callback: () => this.openMain("today")});
    this.addCommand({id: "assistant", name: "Agent: 打开 AI 助手", callback: () => this.openMain("assistant")});
    this.addCommand({id: "expand-idea", name: "Agent: 展开当前灵感", callback: async () => { const file = this.app.workspace.getActiveFile(); if (!file) { new Notice("请先打开一条灵感"); return; } await this.client.post("/jobs", {kind: "expand-idea", payload: {path: file.path}}); new Notice("灵感扩展已加入安全准备队列"); }});
    this.addCommand({id: "next-week", name: "Agent: 生成周计划", callback: async () => { await this.client.post("/jobs", {kind: "learning-plan", payload: {period: "next-week"}}); await this.openMain("plan"); }});
    this.addCommand({id: "restart-agent", name: "Agent: 重启本地服务", callback: async () => { await this.manager?.restart(); new Notice(this.manager?.status === "online" ? "Agent 已重启" : `Agent 状态：${this.manager?.status}`); }});
    this.addCommand({id: "diagnostics", name: "Agent: 打开主脑诊断", callback: () => new BrainDiagnosticsModal(this.app, this.client).open()});
    this.addCommand({id: "brain-recent", name: "Agent: 查看最近执行", callback: () => new BrainDiagnosticsModal(this.app, this.client).open()});
    this.addCommand({id: "brain-retry-latest", name: "Agent: 重试最近失败任务", callback: async () => {
      const result = await this.client.get<any>("/brain/runs?status=failed&limit=1");
      if (!result.items?.length) { new Notice("没有可重试的失败任务"); return; }
      await this.client.post(`/brain/runs/${encodeURIComponent(result.items[0].id)}/retry`, {}); await this.openMain("assistant");
    }});
    this.addCommand({id: "brain-cancel-current", name: "Agent: 取消当前任务", callback: async () => {
      const result = await this.client.get<any>("/brain/runs?limit=20");
      const active = result.items?.find((run: any) => !["completed", "failed", "cancelled"].includes(run.status));
      if (!active) { new Notice("当前没有可取消任务"); return; }
      await this.client.post(`/brain/runs/${encodeURIComponent(active.id)}/cancel`, {}); new Notice("任务已取消");
    }});
    this.addCommand({id: "model-test", name: "Agent: 测试模型连接", callback: () => new ModelConnectionModal(this.app, this.client).open()});
    this.addCommand({id: "curriculum-refresh", name: "Agent: 刷新课程候选池", callback: async () => { await this.client.post("/curriculum/refresh", {}); new Notice("课程候选池已刷新"); await this.openMain("plan"); }});
    this.addCommand({id: "daily-diagnostics", name: "Agent: 打开每日智能诊断", callback: () => new DailyDiagnosticsModal(this.app, this.client).open()});
    this.addCommand({id: "learning-profile-rebuild", name: "Agent: 重建学习画像", callback: async () => { await this.client.post("/learning/profile/rebuild", {}); new Notice("学习画像已重建"); }});
    this.addCommand({id: "export-diagnostics", name: "Agent: 导出脱敏诊断包", callback: async () => {
      const diagnostics = await this.client.get<any>("/brain/diagnostics");
      const folder = "90-Local-Only/AgentLogs"; if (!await this.app.vault.adapter.exists(folder)) await this.app.vault.adapter.mkdir(folder);
      const path = `${folder}/brain-diagnostics-${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
      await this.app.vault.adapter.write(path, JSON.stringify(diagnostics, null, 2)); new Notice(`脱敏诊断已保存：${path}`);
    }});
    const ribbon=this.addRibbonIcon("brain-circuit", "知序", async () => { await this.openMain("today"); });
    ribbon.addEventListener("dblclick",event=>{event.preventDefault();void this.openMain("assistant");});
    ribbon.addEventListener("contextmenu",event=>{event.preventDefault();const menu=new Menu();menu.addItem(item=>item.setTitle("打开知序首页").setIcon("layout-dashboard").onClick(()=>void this.openMain("today")));menu.addItem(item=>item.setTitle("导入 PDF").setIcon("file-input").onClick(()=>void this.openMain("assistant")));menu.addItem(item=>item.setTitle("打开待审核").setIcon("clipboard-check").onClick(()=>void this.openMain("review")));menu.addItem(item=>item.setTitle("打开助手").setIcon("message-circle").onClick(()=>void this.openMain("assistant")));menu.addSeparator();menu.addItem(item=>item.setTitle("重启 Agent").setIcon("refresh-cw").onClick(()=>void this.manager?.restart()));menu.addItem(item=>item.setTitle("打开诊断").setIcon("stethoscope").onClick(()=>new BrainDiagnosticsModal(this.app,this.client).open()));menu.showAtMouseEvent(event);});
  }
  async openMain(tab: MainTab) {
    const registered = this.app.workspace.getLeavesOfType(MAIN_VIEW);
    let leaf = registered.find(item => item.view instanceof LearningAgentMainView && item.view.containerEl.isConnected && item.view.containerEl.getBoundingClientRect().width > 0);
    if (!leaf) {
      // Restored/hot-reloaded leaves may remain in the layout registry while
      // detached from the visible workspace. Remove them and create a real tab.
      for (const stale of registered) stale.detach();
      await new Promise<void>(resolve => window.setTimeout(resolve, 0));
      leaf = this.app.workspace.getLeaf("tab");
      await leaf.setViewState({type: MAIN_VIEW, active: true});
    }
    if (leaf.view instanceof LearningAgentMainView) leaf.view.setTab(tab);
    await this.app.workspace.revealLeaf(leaf);
  }
  private removeRetiredSidebars(): void {
    for (const viewType of [SIDEBAR_VIEW, LEGACY_SIDEBAR_VIEW]) {
      this.app.workspace.detachLeavesOfType(viewType);
    }
  }
  async saveSettings() { await this.saveData(this.settings); }
  async onunload() {
    for (const leaf of this.app.workspace.getLeavesOfType(MAIN_VIEW)) if (leaf.view instanceof LearningAgentMainView) await leaf.view.flushLearningEvents();
    if (this.settings.stopOnUnload) await this.manager?.stop();
  }
}
