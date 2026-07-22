import type {AgentTool} from "@earendil-works/pi-agent-core";
import {Type, type TSchema} from "typebox";
import type {
  PiPermissionDecision,
  PiPermissionRequest,
  PiRunIdentity,
  PiRuntimeTransport,
  PiToolContract,
} from "./types";
import {PiStallGuard} from "./PiStallGuard";

function contractSchema(contract: PiToolContract): TSchema {
  return Type.Unsafe<Record<string, unknown>>(contract.input_schema as TSchema);
}

const observationEncoder = new TextEncoder();

/** Fields a tool result may expose that let the model continue reading. */
const CONTINUATION_KEYS: ReadonlyArray<readonly [string, string]> = [
  ["next_offset", "next_offset"],
  ["nextOffset", "nextOffset"],
  ["cursor", "cursor"],
  ["next_cursor", "next_cursor"],
  ["nextCursor", "nextCursor"],
  ["truncated", "truncated"],
  ["has_more", "has_more"],
];

/** Copy only pagination fields that the result really exposes. Never invent positions. */
function extractContinuation(content: Record<string, unknown>): Record<string, unknown> {
  const continuation: Record<string, unknown> = {};
  for (const [source, dest] of CONTINUATION_KEYS) {
    if (!Object.prototype.hasOwnProperty.call(content, source)) continue;
    const value = content[source];
    if (value === null || typeof value === "number" || typeof value === "boolean" || typeof value === "string") {
      continuation[dest] = value;
    }
  }
  if (!("truncated" in continuation)) continuation.truncated = true;
  return continuation;
}

/**
 * Always returns a valid JSON string for the model. Never truncates a JSON
 * value at the character level. When the encoded result exceeds the tool's
 * transport budget (consumer-facing byte length, not JS string length), it
 * returns a recoverable partial observation instead of a half string.
 *
 * The backend already validates `max_result_bytes` on the compact-encoded
 * byte length, so the boundary measured here matches that contract exactly.
 */
export function serializeToolObservation(
  contract: PiToolContract,
  response: {content?: unknown},
): string {
  const value = response.content;
  const content: Record<string, unknown> =
    value !== undefined && value !== null && typeof value === "object"
      ? (value as Record<string, unknown>)
      : {value};
  const maxBytes = Number.isFinite(contract.max_result_bytes)
    ? Number(contract.max_result_bytes)
    : Infinity;
  if (maxBytes === Infinity) {
    return JSON.stringify(content, null, 2);
  }
  const bytes = observationEncoder.encode(JSON.stringify(content)).byteLength;
  if (bytes <= maxBytes) {
    return JSON.stringify(content, null, 2);
  }
  console.warn("[pi-tool] 结果过大，需要继续分页读取", {
    tool: contract.name,
    resultBytes: bytes,
    maxResultBytes: maxBytes,
  });
  const observation = {
    ok: false,
    status: "partial",
    code: "tool_result_exceeds_transport_budget",
    message: "结果超过当前工具传输预算，请使用分页参数继续读取。",
    tool: contract.name,
    resultBytes: bytes,
    maxResultBytes: maxBytes,
    continuation: extractContinuation(content),
  };
  return JSON.stringify(observation, null, 2);
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

const PERMISSION_ERROR_CODES = new Set([
  "authorization_required",
  "task_authorization_required",
  "permission_required",
  "scope_expansion_required",
  "task_scope_expansion_requires_new_user_turn",
  "task_create_root_requires_scope_expansion",
  "task_organization_scope_required",
  "developer_workspace_not_authorized",
  "developer_workspace_run_mismatch",
  "developer_operation_not_authorized",
  "network_authorization_required",
  "developer_network_scope_expansion_required",
]);

function permissionPayload(error: Record<string, unknown>): Record<string, unknown> | undefined {
  const value = error.permissionRequest ?? error.permission_request ?? error.requiredScope ?? error.required_scope;
  return value && typeof value === "object" ? value as Record<string, unknown> : undefined;
}

function requestedWrites(
  structured: Record<string, unknown> | undefined,
  argumentsRecord: Record<string, unknown>,
): Array<Record<string, unknown>> {
  const raw = Array.isArray(structured?.writes)
    ? structured.writes
    : Array.isArray(argumentsRecord.writes)
      ? argumentsRecord.writes
      : [];
  const writes = raw.filter(item => item && typeof item === "object") as Array<Record<string, unknown>>;
  if (writes.length) return writes;
  const paths = [
    argumentsRecord.path,
    argumentsRecord.source_path,
    argumentsRecord.destination_path,
    structured?.path,
    structured?.projectPath,
  ].map(value => String(value ?? "").trim()).filter(Boolean);
  return [...new Set(paths)].map(path => ({path, action: "access"}));
}

function requestedOrganization(
  structured: Record<string, unknown> | undefined,
  argumentsRecord: Record<string, unknown>,
): PiPermissionRequest["organization"] | undefined {
  const raw = structured?.organization && typeof structured.organization === "object"
    ? structured.organization as Record<string, unknown>
    : argumentsRecord;
  const directories = Array.isArray(raw.directories)
    ? raw.directories.map(value => String(value ?? "").trim()).filter(Boolean)
    : [];
  const moves = (Array.isArray(raw.moves) ? raw.moves : []).flatMap(value => {
    if (!value || typeof value !== "object") return [];
    const item = value as Record<string, unknown>;
    const source = String(item.source_path ?? item.from ?? "").trim();
    const target = String(item.target_path ?? item.destination_path ?? item.to ?? "").trim();
    return source && target ? [{source_path: source, target_path: target}] : [];
  });
  return directories.length || moves.length ? {
    directories,
    moves,
    remove_empty_source_dirs: raw.remove_empty_source_dirs === true,
  } : undefined;
}

function requestedCapability(
  code: string,
  contract: PiToolContract,
  argumentsRecord: Record<string, unknown>,
  structured: Record<string, unknown> | undefined,
): PiPermissionRequest["capability"] {
  const rawType = String(structured?.type ?? "");
  if (rawType === "vault_organization" || code === "task_organization_scope_required") {
    return {type: "vault_organization", toolName: contract.name};
  }
  if (rawType === "developer_workspace" || code.startsWith("developer_")) {
    return {
      type: "developer_workspace",
      workspaceId: String(structured?.workspaceId ?? structured?.workspace_id ?? argumentsRecord.workspace_id ?? ""),
      projectPath: String(structured?.projectPath ?? structured?.project_path ?? ""),
      toolName: contract.name,
    };
  }
  if (rawType === "network" || code.includes("network")) {
    return {type: "network", toolName: contract.name};
  }
  return {type: "vault_writes", toolName: contract.name};
}

/** Converts validated backend contracts into Pi tools without exposing file APIs. */
export function createPiTools(
  contracts: PiToolContract[],
  identity: PiRunIdentity,
  transport: PiRuntimeTransport,
  stallGuard: PiStallGuard,
  requestPermission?: (
    request: PiPermissionRequest,
    signal?: AbortSignal,
  ) => Promise<PiPermissionDecision>,
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
        const call = () => withTimeout(
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
        let response = check.cached
          ? {ok: true, content: stallGuard.repeated(check.cached)}
          : await call();
        if (signal?.aborted) throw new DOMException("Tool call aborted", "AbortError");
        if (response.ok !== true || response.isError === true) {
          const error = response.error as Record<string, unknown> | undefined;
          const code = String(error?.code ?? "runtime_tool_failed");
          const message = String(error?.message ?? "Tool execution failed");
          const structured = error ? permissionPayload(error) : undefined;
          if (requestPermission && (Boolean(structured) || PERMISSION_ERROR_CODES.has(code))) {
            const organization = requestedOrganization(structured, argumentsRecord);
            const fallbackWrites = requestedWrites(structured, argumentsRecord);
            const organizationWrites = organization
              ? [
                  ...organization.directories.map(path => ({path, action: "create_directory"})),
                  ...organization.moves.map(item => ({
                    source_path: item.source_path,
                    target_path: item.target_path,
                    action: "move",
                  })),
                ]
              : [];
            const decision = await requestPermission({
              code,
              message,
              toolName: contract.name,
              toolCallId,
              writes: organizationWrites.length ? organizationWrites : fallbackWrites,
              organization,
              capability: requestedCapability(code, contract, argumentsRecord, structured),
            }, signal);
            if (decision === "deny") {
              const observation = {
                ok: false,
                status: "blocked",
                code: "user_denied_permission",
                message: "用户未授权该操作。请保持当前目标，改用已授权能力或清楚说明无法继续的部分。",
                tool: contract.name,
              };
              return {
                content: [{type: "text", text: serializeToolObservation(contract, {content: observation})}],
                details: {
                  tool: contract.name,
                  result: observation,
                  permissionLevel: contract.permission_level,
                  idempotent: contract.idempotent,
                  blocked: true,
                  status: "blocked",
                },
              };
            }
            if (signal?.aborted) throw new DOMException("Tool call aborted", "AbortError");
            // Retry the exact same tool call after the same Run authorization
            // was expanded. The original id is also the idempotency boundary.
            response = await call();
            if (response.ok !== true || response.isError === true) {
              const retryError = response.error as Record<string, unknown> | undefined;
              throw new Error(`${String(retryError?.code ?? "runtime_tool_failed")}: ${String(retryError?.message ?? "Tool execution failed")}`);
            }
          } else {
            throw new Error(`${code}: ${message}`);
          }
        }
        const content = response.content && typeof response.content === "object"
          ? response.content as Record<string, unknown>
          : {value: response.content};
        if (!check.cached) stallGuard.remember(check.key, content);
        return {
          content: [{type: "text", text: serializeToolObservation(contract, {content})}],
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
