import {
  createAssistantMessageEventStream,
  type AssistantMessageEventStream,
  type AssistantMessage,
  type Context,
  type Model,
  type SimpleStreamOptions,
  type ToolCall,
} from "@earendil-works/pi-ai";
import type {PiRunIdentity, PiRuntimeTransport} from "./types";
import type {PiStallGuard} from "./PiStallGuard";

const EMPTY_USAGE = {
  input: 0,
  output: 0,
  cacheRead: 0,
  cacheWrite: 0,
  totalTokens: 0,
  cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0},
};

function usage(raw: unknown): AssistantMessage["usage"] {
  const value = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const input = Number(value.prompt_tokens ?? value.input_tokens ?? 0);
  const output = Number(value.completion_tokens ?? value.output_tokens ?? 0);
  const reasoning = Number((value.completion_tokens_details as any)?.reasoning_tokens ?? 0);
  return {...EMPTY_USAGE, input, output, reasoning, totalTokens: Number(value.total_tokens ?? input + output)};
}

function parseArguments(raw: string): Record<string, unknown> {
  if (!raw.trim()) return {};
  const value = JSON.parse(raw);
  if (!value || Array.isArray(value) || typeof value !== "object") throw new Error("Tool arguments must be a JSON object");
  return value as Record<string, unknown>;
}

function findLastContentIndex(
  content: AssistantMessage["content"],
  type: "text" | "thinking",
): number {
  for (let index = content.length - 1; index >= 0; index -= 1) {
    if (content[index]?.type === type) return index;
  }
  return -1;
}

type PiModelTimeoutConfig = {
  firstEventMs: number;
  idleMs: number;
  idleDeepMs: number;
  hardMs: number;
  hardDeepMs: number;
};

const DEFAULT_MODEL_TIMEOUTS: PiModelTimeoutConfig = {
  firstEventMs: 45_000,
  idleMs: 90_000,
  idleDeepMs: 180_000,
  hardMs: 15 * 60_000,
  hardDeepMs: 30 * 60_000,
};

/** Secure one-request stream function used by Pi Agent Core. */
export class PiModelTransport {
  private readonly timeouts: PiModelTimeoutConfig;
  constructor(private readonly transport: PiRuntimeTransport, timeouts: Partial<PiModelTimeoutConfig> = {}) {
    this.timeouts = {...DEFAULT_MODEL_TIMEOUTS, ...timeouts};
  }

  stream(identity: PiRunIdentity, model: Model<any>, context: Context, options: SimpleStreamOptions = {}, stallGuard?: PiStallGuard): AssistantMessageEventStream {
    stallGuard?.beforeModelRequest();
    const stream = createAssistantMessageEventStream();
    const partial: AssistantMessage = {
      role: "assistant",
      content: [],
      api: model.api,
      provider: model.provider,
      model: model.id,
      usage: {...EMPTY_USAGE},
      stopReason: "stop",
      timestamp: Date.now(),
    };
    const argumentBuffers = new Map<number, string>();
    const toolContentIndexes = new Map<number, number>();
    let finalUsage = {...EMPTY_USAGE};
    let started = false;
    let terminal = false;
    let providerActivity = false;
    const requestController = new AbortController();
    const forwardAbort = (): void => requestController.abort();
    if (options.signal?.aborted) requestController.abort();
    else options.signal?.addEventListener("abort", forwardAbort, {once: true});
    const deep = Boolean(options?.reasoning);
    const to = this.timeouts;
    let firstTimedOut = false;
    let idleTimedOut = false;
    let hardTimedOut = false;
    const firstTimer = globalThis.setTimeout(() => {
      if (terminal || providerActivity) return;
      firstTimedOut = true;
      requestController.abort();
    }, to.firstEventMs);
    let idleTimer: ReturnType<typeof globalThis.setTimeout> | undefined;
    const resetIdle = (): void => {
      if (terminal) return;
      if (idleTimer) globalThis.clearTimeout(idleTimer);
      idleTimer = globalThis.setTimeout(() => {
        if (terminal) return;
        idleTimedOut = true;
        requestController.abort();
      }, deep ? to.idleDeepMs : to.idleMs);
    };
    const hardTimer = globalThis.setTimeout(() => {
      if (terminal) return;
      hardTimedOut = true;
      requestController.abort();
    }, deep ? to.hardDeepMs : to.hardMs);
    const markProviderActivity = (): void => {
      providerActivity = true;
      globalThis.clearTimeout(firstTimer);
      resetIdle();
    };
    const pushStart = (): void => {
      if (started) return;
      started = true;
      stream.push({type: "start", partial: {...partial, content: [...partial.content]}});
    };
    const pushError = (message: string, code = "model_request_deadline_exceeded"): void => {
      if (terminal) return;
      terminal = true;
      globalThis.clearTimeout(firstTimer);
      if (idleTimer) globalThis.clearTimeout(idleTimer);
      globalThis.clearTimeout(hardTimer);
      pushStart();
      partial.stopReason = options.signal?.aborted ? "aborted" : "error";
      partial.errorMessage = `${code}: ${message}`;
      stream.push({
        type: "error",
        reason: partial.stopReason,
        error: {...partial, content: [...partial.content]},
        code,
      } as unknown as Parameters<typeof stream.push>[0]);
    };

    const resolveFailure = (error?: unknown): void => {
      if (terminal) return;
      let code = "model_request_deadline_exceeded";
      let message: string;
      if (options.signal?.aborted && !firstTimedOut && !idleTimedOut && !hardTimedOut) {
        code = "model_request_aborted";
        message = "模型请求已被用户中止";
      } else if (firstTimedOut) {
        code = "model_first_event_timeout";
        message = "连接模型超时（45 秒内未收到内容或工具事件）；请重试或切换模型";
      } else if (idleTimedOut) {
        code = "model_idle_timeout";
        message = "模型流空闲超时（未在预算内继续产出内容或工具事件）；请重试";
      } else if (hardTimedOut) {
        code = "model_request_deadline_exceeded";
        message = "模型请求超过总时限；请重试或拆分任务";
      } else {
        message = error instanceof Error ? error.message : (error ? String(error) : "模型流意外结束，未收到完成事件；请重试");
      }
      pushError(message, code);
    };

    void this.transport.streamModelProxy({
      profileId: identity.profileId,
      model: identity.model || model.id,
      context,
      options: {
        temperature: options.temperature,
        maxTokens: options.maxTokens,
        reasoning: options.reasoning,
        sessionId: identity.sessionId,
      },
    }, event => {
      const type = String(event.type ?? "");
      markProviderActivity();
      if (type === "start") {
        pushStart();
        return;
      }
      pushStart();
      if (type === "text_start") {
        const contentIndex = partial.content.length;
        partial.content.push({type: "text", text: ""});
        stream.push({type: "text_start", contentIndex, partial: {...partial, content: [...partial.content]}});
      } else if (type === "text_delta") {
        const contentIndex = findLastContentIndex(partial.content, "text");
        const block = partial.content[contentIndex];
        const delta = String(event.delta ?? "");
        if (block?.type === "text") block.text += delta;
        stream.push({type: "text_delta", contentIndex, delta, partial: {...partial, content: [...partial.content]}});
      } else if (type === "text_end") {
        const contentIndex = findLastContentIndex(partial.content, "text");
        const block = partial.content[contentIndex];
        const content = block?.type === "text" ? block.text : "";
        stallGuard?.noteFinalText(content);
        stream.push({type: "text_end", contentIndex, content, partial: {...partial, content: [...partial.content]}});
      } else if (type === "thinking_start") {
        const contentIndex = partial.content.length;
        partial.content.push({type: "thinking", thinking: ""});
        stream.push({type: "thinking_start", contentIndex, partial: {...partial, content: [...partial.content]}});
      } else if (type === "thinking_delta") {
        const contentIndex = findLastContentIndex(partial.content, "thinking");
        const block = partial.content[contentIndex];
        const delta = String(event.delta ?? "");
        if (block?.type === "thinking") block.thinking += delta;
        stream.push({type: "thinking_delta", contentIndex, delta, partial: {...partial, content: [...partial.content]}});
      } else if (type === "thinking_end") {
        const contentIndex = findLastContentIndex(partial.content, "thinking");
        const block = partial.content[contentIndex];
        stream.push({type: "thinking_end", contentIndex, content: block?.type === "thinking" ? block.thinking : "", partial: {...partial, content: [...partial.content]}});
      } else if (type === "tool_call_start") {
        const toolIndex = Number(event.index ?? toolContentIndexes.size);
        const contentIndex = partial.content.length;
        const toolCall: ToolCall = {type: "toolCall", id: String(event.id ?? ""), name: String(event.name ?? ""), arguments: {}};
        partial.content.push(toolCall);
        argumentBuffers.set(toolIndex, "");
        toolContentIndexes.set(toolIndex, contentIndex);
        stream.push({type: "toolcall_start", contentIndex, partial: {...partial, content: [...partial.content]}});
      } else if (type === "tool_call_delta") {
        const index = Number(event.index ?? 0);
        const contentIndex = toolContentIndexes.get(index) ?? -1;
        const delta = String(event.delta ?? "");
        argumentBuffers.set(index, (argumentBuffers.get(index) ?? "") + delta);
        stream.push({type: "toolcall_delta", contentIndex, delta, partial: {...partial, content: [...partial.content]}});
      } else if (type === "tool_call_end") {
        const index = Number(event.index ?? 0);
        const contentIndex = toolContentIndexes.get(index) ?? -1;
        const toolCall = partial.content[contentIndex] as ToolCall | undefined;
        if (!toolCall) throw new Error("Tool call stream ended before it started");
        toolCall.arguments = parseArguments(String(event.arguments ?? argumentBuffers.get(index) ?? ""));
        stream.push({type: "toolcall_end", contentIndex, toolCall: {...toolCall}, partial: {...partial, content: [...partial.content]}});
      } else if (type === "usage") {
        finalUsage = usage(event.usage);
        partial.usage = finalUsage;
      } else if (type === "done") {
        terminal = true;
        if (idleTimer) globalThis.clearTimeout(idleTimer);
        globalThis.clearTimeout(hardTimer);
        partial.usage = finalUsage;
        partial.stopReason = String(event.finishReason ?? "stop") as "stop" | "length" | "toolUse";
        stallGuard?.noteFinalText(partial.content
          .filter((block): block is Extract<AssistantMessage["content"][number], {type: "text"}> => block.type === "text")
          .map(block => block.text)
          .join("\n"));
        stream.push({type: "done", reason: partial.stopReason, message: {...partial, content: [...partial.content]}});
      } else if (type === "error" || type === "run.failed") {
        pushError(String(event.message ?? event.code ?? "Model proxy failed"));
      }
    }, requestController.signal).then(() => {
      resolveFailure();
    }).catch(error => {
      resolveFailure(error);
    }).finally(() => {
      globalThis.clearTimeout(firstTimer);
      if (idleTimer) globalThis.clearTimeout(idleTimer);
      globalThis.clearTimeout(hardTimer);
      options.signal?.removeEventListener("abort", forwardAbort);
    });
    return stream;
  }
}
