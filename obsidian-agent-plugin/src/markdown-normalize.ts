const PROTECTED_MARKDOWN = /(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`|https?:\/\/[^\s<>]+|\$\$[\s\S]*?\$\$|\$(?:\\.|[^$\n])+\$)/g;

function normalizeSegment(value: string): string {
  return value
    .replace(/\\\[\s*([\s\S]*?)\s*\\\]/g, (_match, expression: string) => `$$\n${expression.trim()}\n$$`)
    .replace(/\\\(\s*([\s\S]*?)\s*\\\)/g, (_match, expression: string) => `$${expression.trim()}$`);
}

/** Normalize common model math delimiters without touching code, URLs or valid math. */
export function normalizeAssistantMarkdown(markdown: string): string {
  const source = String(markdown ?? "");
  let cursor = 0;
  let result = "";
  for (const match of source.matchAll(PROTECTED_MARKDOWN)) {
    const index = match.index ?? 0;
    result += normalizeSegment(source.slice(cursor, index));
    result += match[0];
    cursor = index + match[0].length;
  }
  return result + normalizeSegment(source.slice(cursor));
}
