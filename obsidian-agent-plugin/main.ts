import {App, ItemView, Modal, Notice, Plugin, WorkspaceLeaf} from "obsidian";
import {AgentClient} from "./src/api";

const VIEW_TYPE = "obsidian-learning-agent-view";

class TextPrompt extends Modal {
  constructor(app: App, private titleText: string, private done: (value: string) => void) { super(app); }
  onOpen() {
    this.contentEl.createEl("h3", {text: this.titleText});
    const input = this.contentEl.createEl("input", {type: "text"}); input.style.width = "100%";
    const button = this.contentEl.createEl("button", {text: "确认"});
    button.onclick = () => { const value = input.value.trim(); if (value) { this.close(); this.done(value); } };
  }
}

class ContentModal extends Modal {
  constructor(app: App, private titleText: string, private content: string, private confirmText?: string, private confirmed?: () => Promise<void>) { super(app); }
  onOpen() {
    this.contentEl.createEl("h2", {text: this.titleText});
    const pre = this.contentEl.createEl("pre", {text: this.content}); pre.style.maxHeight = "60vh"; pre.style.overflow = "auto"; pre.style.whiteSpace = "pre-wrap";
    if (this.confirmText && this.confirmed) {
      const button = this.contentEl.createEl("button", {text: this.confirmText});
      button.onclick = async () => { if (!window.confirm("确认执行该操作？")) return; await this.confirmed!(); this.close(); };
    }
  }
}

class AgentView extends ItemView {
  constructor(leaf: WorkspaceLeaf, private client: AgentClient) { super(leaf); }
  getViewType() { return VIEW_TYPE; }
  getDisplayText() { return "学习 Agent"; }
  async onOpen() { await this.refresh(); }
  private button(parent: HTMLElement, text: string, action: () => Promise<void>) {
    const button = parent.createEl("button", {text}); button.onclick = () => action().catch(error => new Notice(`Agent 错误：${error.message}`));
  }
  async refresh() {
    const root = this.containerEl.children[1] as HTMLElement; root.empty(); root.createEl("h2", {text: "学习 Agent"});
    let health: any;
    try {
      health = await this.client.health();
      root.createEl("p", {text: `服务：在线 · 协议 v${health.protocol_version}`});
    } catch (error: any) {
      root.createEl("p", {text: `本地 Agent 服务未运行：${error.message}`, cls: "mod-warning"});
      return;
    }
    try {
      const [prepared, reviews, jobs, learning] = await Promise.all([
        this.client.get<any>("/prepared"), this.client.get<any>("/reviews"),
        this.client.get<any>("/jobs"), this.client.get<any>("/learning/today"),
      ]);
      root.createEl("h3", {text: `待处理资料（${prepared.bundles.filter((x: any) => x.state === "prepared").length}）`});
      for (const bundle of prepared.bundles) {
        const row = root.createDiv(); row.createSpan({text: `${bundle.prepared_id} · ${bundle.state} `});
        this.button(row, "查看 Change Set", async () => {
          const data = await this.client.get<any>(`/prepared/${encodeURIComponent(bundle.prepared_id)}`);
          new ContentModal(this.app, `Change Set · ${bundle.prepared_id}`, data.preview, bundle.state === "prepared" ? "确认并应用" : undefined, bundle.state === "prepared" ? async () => { await this.client.post("/prepared/apply", {prepared_id: bundle.prepared_id}); await this.refresh(); } : undefined).open();
        });
        if (bundle.state === "prepared") this.button(row, "确认并应用", async () => {
          if (!window.confirm("确认应用这个已检查的 Prepared Bundle？此操作不会调用模型。")) return;
          await this.client.post("/prepared/apply", {prepared_id: bundle.prepared_id}); await this.refresh();
        });
      }
      root.createEl("h3", {text: `待审核草稿（${reviews.artifacts.filter((x: any) => x.review_state === "pending").length}）`});
      for (const artifact of reviews.artifacts) {
        const row = root.createDiv(); row.createSpan({text: `${artifact.artifact_role} · ${artifact.artifact_id} `});
        this.button(row, "Diff", async () => { const data = await this.client.get<any>(`/reviews/${encodeURIComponent(artifact.artifact_id)}/diff`); new ContentModal(this.app, `Diff · ${artifact.artifact_id}`, data.diff).open(); });
        if (artifact.status === "ai-draft") {
          this.button(row, "接受", async () => { await this.client.post("/review/transition", {artifact_id: artifact.artifact_id, action: "approve"}); await this.refresh(); });
          this.button(row, "修改后接受", async () => { await this.client.post("/review/transition", {artifact_id: artifact.artifact_id, action: "approve-edited"}); await this.refresh(); });
          this.button(row, "拒绝", async () => new TextPrompt(this.app, "拒绝原因", async reason => { await this.client.post("/review/transition", {artifact_id: artifact.artifact_id, action: "reject", reason}); await this.refresh(); }).open());
        }
      }
      root.createEl("h3", {text: `当前任务（${jobs.jobs.length}）`});
      for (const job of jobs.jobs.slice(0, 10)) root.createEl("div", {text: `${job.kind} · ${job.state}`});
      root.createEl("h3", {text: "今日复习"});
      for (const item of learning.review) root.createEl("div", {text: `${item.title} · mastery ${item.mastery}`});
      root.createEl("h3", {text: "今日学习"});
      for (const item of learning.new_learning) root.createEl("div", {text: `${item.title} · ${item.domain}`});
      root.createEl("h3", {text: "未来两天"});
      for (const item of learning.next_two_days) root.createEl("div", {text: `${item.title} · ${item.next_review}`});
    } catch (error: any) {
      root.createEl("p", {text: `服务在线，但面板数据加载失败：${error.message}`, cls: "mod-warning"});
    }
  }
}

export default class LearningAgentPlugin extends Plugin {
  client = new AgentClient();
  async onload() {
    this.registerView(VIEW_TYPE, leaf => new AgentView(leaf, this.client));
    const open = async () => { let leaf = this.app.workspace.getLeavesOfType(VIEW_TYPE)[0]; if (!leaf) { leaf = this.app.workspace.getRightLeaf(false)!; await leaf.setViewState({type: VIEW_TYPE, active: true}); } else if (leaf.view instanceof AgentView) { await leaf.view.refresh(); } this.app.workspace.revealLeaf(leaf); };
    this.addCommand({id: "import-pdf", name: "Agent: 导入 PDF", callback: () => new TextPrompt(this.app, "本地 PDF 路径", async pdf => { await this.client.post("/jobs", {kind: "prepare-pdf", payload: {pdf}}); new Notice("已加入处理队列"); }).open()});
    this.addCommand({id: "view-jobs", name: "Agent: 查看任务", callback: open});
    this.addCommand({id: "review", name: "Agent: 审核待处理内容", callback: open});
    this.addCommand({id: "expand-idea", name: "Agent: 展开当前灵感", callback: async () => { const file = this.app.workspace.getActiveFile(); if (!file) return new Notice("请先打开一条灵感"); await this.client.post("/jobs", {kind: "expand-idea", payload: {path: file.path}}); }});
    this.addCommand({id: "today-learning", name: "Agent: 开始今日学习", callback: open});
    this.addCommand({id: "next-week", name: "Agent: 生成下周计划", callback: async () => { await this.client.post("/jobs", {kind: "learning-plan", payload: {period: "next-week"}}); new Notice("已加入计划队列"); }});
    this.addRibbonIcon("brain", "学习 Agent", open);
  }
}
