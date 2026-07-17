export interface InlineAgentConfirmation {
  run_id: string;
  proposal_id: string;
  title: string;
  summary: string;
  risk_level: "medium" | "high";
  writes: Array<Record<string, unknown>>;
  tool_name?: string;
  actions: Array<"confirm" | "reject">;
}

export interface InlineConfirmationHandlers {
  confirm: (runId: string) => Promise<void>;
  reject: (runId: string) => Promise<void>;
  openDiff?: (proposalId: string) => Promise<void>;
}

export function renderInlineAgentConfirmation(
  container: HTMLElement,
  confirmation: InlineAgentConfirmation,
  handlers: InlineConfirmationHandlers,
): () => void {
  const card = container.createDiv({
    cls: "la-inline-agent-confirmation",
  });
  card.setAttribute("role", "group");
  card.setAttribute(
    "aria-label",
    "Agent 请求确认一次 Obsidian 修改",
  );

  const icon = card.createDiv({
    cls: "la-inline-agent-confirmation__icon",
    text: "✦",
  });
  icon.setAttribute("aria-hidden", "true");

  const body = card.createDiv({
    cls: "la-inline-agent-confirmation__body",
  });
  body.createDiv({
    cls: "la-inline-agent-confirmation__title",
    text: confirmation.title,
  });
  body.createDiv({
    cls: "la-inline-agent-confirmation__tool",
    text: `工具 · ${confirmation.tool_name ?? "commit_vault_change"}`,
  });
  body.createDiv({
    cls: "la-inline-agent-confirmation__summary",
    text: confirmation.summary,
  });

  const meta = body.createDiv({
    cls: "la-inline-agent-confirmation__meta",
  });
  meta.setText(
    `${confirmation.writes.length} 个文件变更 · ${
      confirmation.risk_level === "high" ? "高风险" : "需要确认"
    }`,
  );
  if (confirmation.writes.length) {
    const files = body.createEl("ul", {
      cls: "la-inline-agent-confirmation__files",
    });
    for (const write of confirmation.writes) {
      files.createEl("li", {
        text: `${String(write.action ?? "update")} · ${String(write.path ?? "")}`,
      });
    }
  }

  const actions = body.createDiv({
    cls: "la-inline-agent-confirmation__actions",
  });

  let disposed = false;
  let busy = false;

  const setBusy = (value: boolean) => {
    busy = value;
    confirmButton.disabled = value;
    rejectButton.disabled = value;
    diffButton && (diffButton.disabled = value);
  };

  let diffButton: HTMLButtonElement | undefined;
  if (handlers.openDiff && confirmation.proposal_id) {
    diffButton = actions.createEl("button", {
      cls: "la-button la-button--secondary",
      text: "查看变化",
    });
    diffButton.addEventListener("click", () => {
      if (!busy && !disposed) {
        void handlers.openDiff?.(confirmation.proposal_id);
      }
    });
  }

  const rejectButton = actions.createEl("button", {
    cls: "la-button la-button--ghost",
    text: "取消",
  });
  rejectButton.addEventListener("click", async () => {
    if (busy || disposed) return;
    setBusy(true);
    try {
      await handlers.reject(confirmation.run_id);
      card.addClass("is-resolved");
      card.dataset.state = "rejected";
    } finally {
      setBusy(false);
    }
  });

  const confirmButton = actions.createEl("button", {
    cls: "la-button la-button--primary",
    text: "确认执行",
  });
  confirmButton.addEventListener("click", async () => {
    if (busy || disposed) return;
    setBusy(true);
    confirmButton.setText("执行中…");
    try {
      await handlers.confirm(confirmation.run_id);
      card.addClass("is-resolved");
      card.dataset.state = "confirmed";
      confirmButton.setText("已确认");
    } catch (error) {
      confirmButton.setText("重试确认");
      const message = error instanceof Error ? error.message : String(error);
      let errorNode = body.querySelector<HTMLElement>(".la-inline-agent-confirmation__error");
      if (!errorNode) errorNode = body.createDiv({cls: "la-inline-agent-confirmation__error"});
      errorNode.setText(message);
    } finally {
      setBusy(false);
    }
  });

  return () => {
    disposed = true;
    card.remove();
  };
}
