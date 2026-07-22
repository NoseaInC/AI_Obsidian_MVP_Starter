import {
  estimateContextTokens,
  estimateTokens,
  type AgentMessage,
} from "@earendil-works/pi-agent-core";

export interface PiCompactionResult {
  messages: AgentMessage[];
  compacted: boolean;
  tokensBefore: number;
  keptTokens: number;
  summary: string;
}

const DEFAULT_CONTEXT_WINDOW = 128_000;
const DEFAULT_RESERVE = 24_000;
const DEFAULT_KEEP_RECENT = 28_000;

function textOf(message: AgentMessage): string {
  const raw = message as any;
  if (typeof raw.content === "string") return raw.content;
  if (!Array.isArray(raw.content)) return "";
  return raw.content
    .filter((item: any) => item?.type === "text")
    .map((item: any) => String(item.text ?? ""))
    .join("\n");
}

function safeLine(value: string, max = 320): string {
  return value.replace(/(?:sk-|api[_-]?key|authorization)\S*/gi, "[已隐藏]")
    .replace(/\s+/g, " ").trim().slice(0, max);
}

function isUser(message: AgentMessage): boolean {
  return (message as any).role === "user";
}

function isPendingBoundary(message: AgentMessage): boolean {
  const raw = JSON.stringify(message);
  return raw.includes("question_required") || raw.includes("scope_expansion_required") || raw.includes("confirmation_required");
}

/** Token-aware compaction with cuts only at complete user-turn boundaries. */
export function compactAgentMessages(
  messages: AgentMessage[],
  contextWindow = DEFAULT_CONTEXT_WINDOW,
  reserveTokens = DEFAULT_RESERVE,
  keepRecentTokens = DEFAULT_KEEP_RECENT,
  force = false,
): PiCompactionResult {
  const tokensBefore = estimateContextTokens(messages).tokens;
  if (!force && tokensBefore <= contextWindow - reserveTokens) {
    return {messages, compacted: false, tokensBefore, keptTokens: tokensBefore, summary: ""};
  }
  let keptTokens = 0;
  let cut = -1;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    keptTokens += estimateTokens(messages[index]);
    if (keptTokens >= keepRecentTokens && isUser(messages[index]) && !isPendingBoundary(messages[index])) {
      cut = index;
      break;
    }
  }
  if (cut <= 0) return {messages, compacted: false, tokensBefore, keptTokens: tokensBefore, summary: ""};
  const old = messages.slice(0, cut);
  const users = old.filter(isUser).map(textOf).filter(Boolean);
  const toolFailures = old
    .filter(item => (item as any).role === "toolResult" && (item as any).isError)
    .map(textOf).filter(Boolean);
  const summary = [
    "<zhixu_compaction_summary>",
    `当前目标与用户约束：${safeLine(users.length ? users[users.length - 1] : "继续当前会话目标")}`,
    `更早目标：${users.slice(-4, -1).map(item => safeLine(item, 180)).join("；") || "无"}`,
    "已完成内容：较早 Turn 已压缩；后续完整 Turn、工具配对与 Action 引用保留。",
    "未完成内容：以保留的最近 Turn、pending 请求和当前 Task Authorization 为准。",
    `工具失败与恢复：${toolFailures.slice(-3).map(item => safeLine(item, 160)).join("；") || "无待处理失败"}`,
    "安全约束：不得从摘要恢复密钥、隐藏推理或已省略的敏感正文；需要事实时重新调用受控工具。",
    "</zhixu_compaction_summary>",
  ].join("\n");
  const summaryMessage = {role: "user", content: summary, timestamp: Date.now()} as AgentMessage;
  const compactedMessages = [summaryMessage, ...messages.slice(cut)];
  return {
    messages: compactedMessages,
    compacted: true,
    tokensBefore,
    keptTokens: estimateContextTokens(compactedMessages).tokens,
    summary,
  };
}
