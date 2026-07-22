export interface InlineAgentConfirmation {
  run_id: string;
  kind: "write" | "question" | "permission";
  proposal_id: string;
  title: string;
  summary: string;
  risk_level: "low" | "medium" | "high";
  writes: Array<Record<string, unknown>>;
  tool_name?: string;
  question: string;
  options: string[];
  reason: string;
  scope_candidates: string[];
  actions: string[];
}

export interface InlineConfirmationHandlers {
  confirm: (runId: string, scope?: string) => Promise<void>;
  reject: (runId: string) => Promise<void>;
  answer: (runId: string, answer: string) => Promise<void>;
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
    confirmation.kind === "question" ? "知序需要用户回答" : "知序 Harness 请求一次操作授权",
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
    text: `Harness · ${confirmation.tool_name ?? "commit_vault_change"}`,
  });
  body.createDiv({
    cls: "la-inline-agent-confirmation__summary",
    text: confirmation.summary,
  });

  if (confirmation.kind === "question") {
    body.createDiv({
      cls: "la-inline-agent-confirmation__question",
      text: confirmation.question,
    });
    const actions = body.createDiv({cls: "la-inline-agent-confirmation__actions"});
    let disposed = false;
    let busy = false;
    const controls: HTMLButtonElement[] = [];
    let answerInput: HTMLInputElement | undefined;
    const setBusy = (value: boolean) => {
      busy = value;
      controls.forEach(control => control.disabled = value);
      if (answerInput) answerInput.disabled = value;
    };
    const submit = async (answer: string) => {
      if (busy || disposed || !answer.trim()) return;
      setBusy(true);
      try {
        await handlers.answer(confirmation.run_id, answer.trim());
        card.addClass("is-resolved");
        card.dataset.state = "answered";
      } finally {
        setBusy(false);
      }
    };
    for (const option of confirmation.options ?? []) {
      const choice = actions.createEl("button", {
        cls: "la-button la-button--secondary",
        text: option,
      });
      controls.push(choice);
      choice.addEventListener("click", () => void submit(option));
    }
    if (!(confirmation.options ?? []).length) {
      answerInput = body.createEl("input", {
        cls: "la-inline-agent-confirmation__answer",
        attr: {type: "text", placeholder: "输入回答后继续当前任务"},
      });
      const answerButton = actions.createEl("button", {
        cls: "la-button la-button--primary",
        text: "回答并继续",
      });
      controls.push(answerButton);
      answerButton.addEventListener("click", () => void submit(answerInput?.value ?? ""));
      answerInput.addEventListener("keydown", event => {
        if (event.key === "Enter") {
          event.preventDefault();
          void submit(answerInput?.value ?? "");
        }
      });
      answerInput.focus();
    }
    const cancel = actions.createEl("button", {
      cls: "la-button la-button--ghost",
      text: "取消任务",
    });
    controls.push(cancel);
    cancel.addEventListener("click", async () => {
      if (busy || disposed) return;
      setBusy(true);
      try { await handlers.reject(confirmation.run_id); } finally { setBusy(false); }
    });
    return () => {
      disposed = true;
      card.remove();
    };
  }

  const meta = body.createDiv({
    cls: "la-inline-agent-confirmation__meta",
  });
  meta.setText(confirmation.kind === "permission"
    ? `权限请求 · ${confirmation.tool_name ?? "受控工具"} · ${confirmation.writes.length} 项 · 仅当前任务`
    : `${confirmation.writes.length} 个文件变更 · ${
        confirmation.risk_level === "high" ? "高风险操作" : "仅本次授权"
      }`);
  if (confirmation.writes.length) {
    const files = body.createEl("ul", {
      cls: "la-inline-agent-confirmation__files",
    });
    for (const write of confirmation.writes) {
      const source = String(write.source_path ?? write.from ?? "").trim();
      const destination = String(write.target_path ?? write.destination_path ?? write.to ?? "").trim();
      const path = String(write.path ?? "").trim();
      files.createEl("li", {
        text: source && destination
          ? `${String(write.action ?? "move")} · ${source} → ${destination}`
          : `${String(write.action ?? "update")} · ${path}`,
      });
    }
  }

  const actions = body.createDiv({
    cls: "la-inline-agent-confirmation__actions",
  });

  let disposed = false;
  let busy = false;
  let scopeButton: HTMLButtonElement | undefined;

  const setBusy = (value: boolean) => {
    busy = value;
    confirmButton.disabled = value;
    rejectButton.disabled = value;
    diffButton && (diffButton.disabled = value);
    scopeButton && (scopeButton.disabled = value);
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
    text: confirmation.kind === "permission" ? "不允许，继续" : "取消",
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
    text: "允许本次",
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

  const scope = confirmation.scope_candidates?.[0];
  if (scope) {
    scopeButton = actions.createEl("button", {
      cls: "la-button la-button--secondary",
      text: confirmation.kind === "permission" && scope === "__all__"
        ? "当前任务全部允许"
        : `本会话允许在 ${scope} 新建`,
    });
    scopeButton.addEventListener("click", async () => {
      if (busy || disposed) return;
      setBusy(true);
      try {
        await handlers.confirm(confirmation.run_id, scope);
        card.addClass("is-resolved");
        card.dataset.state = "confirmed-scoped";
      } finally {
        setBusy(false);
      }
    });
  }

  return () => {
    disposed = true;
    card.remove();
  };
}
