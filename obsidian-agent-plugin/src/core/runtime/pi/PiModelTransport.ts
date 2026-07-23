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

type PiModelErrorCode =
  | "model_first_event_timeout"
  | "model_idle_timeout"
  | "model_request_deadline_exceeded"
  | "model_request_aborted"
  | "model_authentication_failed"
  | "model_rate_limited"
  | "model_not_found"
  | "model_context_length_exceeded"
  | "model_provider_error"
  | "model_stream_protocol_error";

const PROVIDER_ERROR_CODES = new Set<PiModelErrorCode>([
  "model_authentication_failed",
  "model_rate_limited",
  "model_not_found",
  "model_context_length_exceeded",
  "model_provider_error",
  "model_stream_protocol_error",
]);

function normalizedModelErrorCode(raw: unknown, message = ""): PiModelErrorCode {
  const code = String(raw ?? "");
  if (PROVIDER_ERROR_CODES.has(code as PiModelErrorCode)) return code as PiModelErrorCode;
  if (
    code.startsWith("model_tool_")
    || code === "model_stream_invalid"
    || /(?:json|tool call stream|protocol|unexpected end|malformed)/i.test(message)
  ) {
    return "model_stream_protocol_error";
  }
  if (/401|403|authentication|unauthorized|forbidden/i.test(`${code} ${message}`)) {
    return "model_authentication_failed";
  }
  if (/429|rate.?limit/i.test(`${code} ${message}`)) return "model_rate_limited";
  if (/model.{0,12}(?:not found|unknown)/i.test(`${code} ${message}`)) return "model_not_found";
  if (/context.{0,20}(?:length|limit)|too many tokens|max(?:imum)? tokens/i.test(`${code} ${message}`)) {
    return "model_context_length_exceeded";
  }
  return "model_provider_error";
}

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
    const deep = Boolean(options?.reasoning);
    const to = this.timeouts;
    let firstTimer: ReturnType<typeof globalThis.setTimeout> | undefined;
    let idleTimer: ReturnType<typeof globalThis.setTimeout> | undefined;
    let hardTimer: ReturnType<typeof globalThis.setTimeout> | undefined;
    const pushStart = (): void => {
      if (started) return;
      started = true;
      stream.push({type: "start", partial: {...partial, content: [...partial.content]}});
    };
    const clearTimers = (): void => {
      if (firstTimer) globalThis.clearTimeout(firstTimer);
      if (idleTimer) globalThis.clearTimeout(idleTimer);
      if (hardTimer) globalThis.clearTimeout(hardTimer);
      firstTimer = undefined;
      idleTimer = undefined;
      hardTimer = undefined;
    };
    const terminate = (
      code: PiModelErrorCode,
      message: string,
      reason: "error" | "aborted",
    ): void => {
      if (terminal) return;
      terminal = true;
      clearTimers();
      options.signal?.removeEventListener("abort", forwardAbort);
      pushStart();
      partial.stopReason = reason;
      partial.errorMessage = `${code}: ${message}`;
      stream.push({
        type: "error",
        reason,
        error: {...partial, content: [...partial.content]},
        code,
      } as unknown as Parameters<typeof stream.push>[0]);
      // The stream already has its terminal event. Abort is only best-effort
      // cleanup; correctness never depends on the provider observing it.
      requestController.abort();
    };
    const resetIdle = (): void => {
      if (terminal) return;
      if (idleTimer) globalThis.clearTimeout(idleTimer);
      idleTimer = globalThis.setTimeout(() => {
        terminate(
          "model_idle_timeout",
          "模型流空闲超时（未在预算内继续产出内容或工具事件）；请重试",
          "error",
        );
      }, deep ? to.idleDeepMs : to.idleMs);
    };
    const markProviderActivity = (): void => {
      providerActivity = true;
      if (firstTimer) globalThis.clearTimeout(firstTimer);
      firstTimer = undefined;
      resetIdle();
    };
    const forwardAbort = (): void => {
      terminate("model_request_aborted", "模型请求已被用户中止", "aborted");
    };
    if (options.signal?.aborted) {
      forwardAbort();
      return stream;
    }
    options.signal?.addEventListener("abort", forwardAbort, {once: true});
    firstTimer = globalThis.setTimeout(() => {
      if (providerActivity) return;
      terminate(
        "model_first_event_timeout",
        "连接模型超时（首个事件预算内未收到内容或工具事件）；请重试或切换模型",
        "error",
      );
    }, to.firstEventMs);
    hardTimer = globalThis.setTimeout(() => {
      terminate("model_request_deadline_exceeded", "模型请求超过总时限；请重试或拆分任务", "error");
    }, deep ? to.hardDeepMs : to.hardMs);

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
      if (terminal) return;
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
        clearTimers();
        options.signal?.removeEventListener("abort", forwardAbort);
        partial.usage = finalUsage;
        partial.stopReason = String(event.finishReason ?? "stop") as "stop" | "length" | "toolUse";
        stallGuard?.noteFinalText(partial.content
          .filter((block): block is Extract<AssistantMessage["content"][number], {type: "text"}> => block.type === "text")
          .map(block => block.text)
          .join("\n"));
        stream.push({type: "done", reason: partial.stopReason, message: {...partial, content: [...partial.content]}});
      } else if (type === "error" || type === "run.failed") {
        const message = String(event.message ?? event.code ?? "Model proxy failed");
        terminate(
          normalizedModelErrorCode(event.code, message),
          message,
          "error",
        );
      }
    }, requestController.signal).then(() => {
      terminate(
        "model_stream_protocol_error",
        "模型流意外结束，未收到完成事件；请重试",
        "error",
      );
    }).catch(error => {
      if (options.signal?.aborted) {
        forwardAbort();
        return;
      }
      const message = error instanceof Error
        ? error.message
        : String(error ?? "模型流异常结束；请重试");
      terminate(
        normalizedModelErrorCode(
          error && typeof error === "object" ? (error as {code?: unknown}).code : "",
          message,
        ),
        message,
        "error",
      );
    }).finally(() => {
      clearTimers();
      options.signal?.removeEventListener("abort", forwardAbort);
    });
    return stream;
  }
}
