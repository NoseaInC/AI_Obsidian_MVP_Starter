import type {AgentTool} from "@earendil-works/pi-agent-core";
import {Type, type TSchema} from "typebox";
import type {
  PiRunIdentity,
  PiRuntimeTransport,
  PiToolContract,
} from "./types";
import {PiStallGuard} from "./PiStallGuard";

function contractSchema(contract: PiToolContract): TSchema {
  return Type.Unsafe<Record<string, unknown>>(contract.input_schema as TSchema);
}

function resultText(value: unknown): string {
  const encoded = JSON.stringify(value, null, 2);
  return encoded.length <= 64_000
    ? encoded
    : `${encoded.slice(0, 64_000)}\n…[tool result truncated by plugin]`;
}

async function withTimeout<T>(promise: Promise<T>, milliseconds: number, signal?: AbortSignal): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let abortHandler: (() => void) | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error("tool_timeout")), milliseconds);
    abortHandler = () => reject(new DOMException("Tool call aborted", "AbortError"));
    signal?.addEventListener("abort", abortHandler, {once: true});
  });
  try {
    return await Promise.race([promise, timeout]);
  } finally {
    if (timer) clearTimeout(timer);
    if (abortHandler) signal?.removeEventListener("abort", abortHandler);
  }
}

/** Converts validated backend contracts into Pi tools without exposing file APIs. */
export function createPiTools(
  contracts: PiToolContract[],
  identity: PiRunIdentity,
  transport: PiRuntimeTransport,
  stallGuard: PiStallGuard,
): AgentTool<any, Record<string, unknown>>[] {
  return contracts
    .filter(contract => ["read_only", "proposal"].includes(contract.permission_level))
    .map(contract => ({
      name: contract.name,
      label: contract.name,
      description: contract.description,
      parameters: contractSchema(contract),
      executionMode: contract.mutates_state ? "sequential" : "parallel",
      execute: async (toolCallId, params, signal) => {
        if (signal?.aborted) throw new DOMException("Tool call aborted", "AbortError");
        const argumentsRecord = params as Record<string, unknown>;
        const check = stallGuard.beforeTool(contract.name, argumentsRecord);
        const response = check.cached
          ? {ok: true, content: stallGuard.repeated(check.cached)}
          : await withTimeout(
            transport.callRuntimeTool({
              runId: identity.runId,
              turnId: identity.turnId,
              toolCallId,
              toolName: contract.name,
              arguments: argumentsRecord,
              taskAuthorizationId: identity.taskAuthorization.id,
              sourceMessageId: identity.sourceMessageId,
              networkAuthorized: identity.taskAuthorization.networkPolicy === "allow",
            }),
            Math.max(1, contract.timeout_seconds) * 1000,
            signal,
          );
        if (signal?.aborted) throw new DOMException("Tool call aborted", "AbortError");
        if (response.ok !== true || response.isError === true) {
          const error = response.error as Record<string, unknown> | undefined;
          const code = String(error?.code ?? "runtime_tool_failed");
          const message = String(error?.message ?? "Tool execution failed");
          throw new Error(`${code}: ${message}`);
        }
        const content = response.content as Record<string, unknown>;
        if (!check.cached) stallGuard.remember(check.key, content);
        return {
          content: [{type: "text", text: resultText(content)}],
          details: {
            tool: contract.name,
            result: content,
            permissionLevel: contract.permission_level,
            idempotent: contract.idempotent,
          },
        };
      },
    }));
}
