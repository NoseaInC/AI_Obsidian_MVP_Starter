export const ASSISTANT_READABLE_ROOTS = [
  "00-Inbox",
  "01-Inbox",
  "10-Sources",
  "20-Knowledge",
  "30-Learning",
  "40-Projects",
] as const;

export function isAssistantReadableVaultPath(path: string): boolean {
  const normalized = path.replace(/^\/+/, "").replace(/\\/g, "/");
  return normalized.toLowerCase().endsWith(".md")
    && ASSISTANT_READABLE_ROOTS.some(root => normalized.startsWith(`${root}/`));
}

export function referencedVaultNotePath(message: string): string {
  const lines = message.split(/\r?\n/).map(line => line.trim());
  for (const line of lines) {
    if (!line.startsWith("@")) continue;
    const path = line.slice(1).trim();
    if (isAssistantReadableVaultPath(path)) return path;
  }
  return "";
}
