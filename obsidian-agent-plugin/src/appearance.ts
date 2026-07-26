export type ZhixuFontPreset =
  | "obsidian"
  | "harmonyos-sans-sc"
  | "lxgw-wenkai"
  | "custom";

export interface ZhixuAppearanceSettings {
  uiFontPreset: ZhixuFontPreset;
  uiFontCustom: string;
  readingFontPreset: ZhixuFontPreset;
  readingFontCustom: string;
  readingFontSize: number;
  readingLineHeight: number;
}

export const DEFAULT_APPEARANCE_SETTINGS: ZhixuAppearanceSettings = {
  uiFontPreset: "obsidian",
  uiFontCustom: "",
  readingFontPreset: "obsidian",
  readingFontCustom: "",
  readingFontSize: 15,
  readingLineHeight: 1.7,
};

const FONT_STACKS: Record<
  Exclude<ZhixuFontPreset, "obsidian" | "custom">,
  string
> = {
  "harmonyos-sans-sc": [
    '"HarmonyOS Sans SC"',
    '"PingFang SC"',
    "-apple-system",
    "BlinkMacSystemFont",
    '"Segoe UI"',
    "sans-serif",
  ].join(", "),

  "lxgw-wenkai": [
    '"LXGW WenKai"',
    '"\u971E\u9E5C\u6587\u6977"',
    '"PingFang SC"',
    "serif",
  ].join(", "),
};

function clamp(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.min(max, Math.max(min, value));
}

/**
 * Custom font input is treated as one font family name, not arbitrary CSS.
 */
export function sanitizeCustomFontName(value: string): string {
  return String(value ?? "")
    .replace(/["'\\;:{}()\n\r\t]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 80);
}

export function resolveZhixuFontStack(
  preset: ZhixuFontPreset,
  customName: string,
  fallbackVariable: "--font-interface" | "--font-text",
): string {
  if (preset === "obsidian") {
    return `var(${fallbackVariable})`;
  }

  if (preset === "custom") {
    const sanitized = sanitizeCustomFontName(customName);
    return sanitized
      ? `"${sanitized}", var(${fallbackVariable})`
      : `var(${fallbackVariable})`;
  }

  return FONT_STACKS[preset];
}

export function normalizeAppearanceSettings(
  settings: ZhixuAppearanceSettings,
): ZhixuAppearanceSettings {
  const VALID_FONT_PRESETS = new Set<ZhixuFontPreset>([
    "obsidian",
    "harmonyos-sans-sc",
    "lxgw-wenkai",
    "custom",
  ]);
  const normalizePreset = (value: unknown): ZhixuFontPreset =>
    VALID_FONT_PRESETS.has(value as ZhixuFontPreset)
      ? (value as ZhixuFontPreset)
      : "obsidian";

  return {
    uiFontPreset: normalizePreset(settings.uiFontPreset),
    uiFontCustom: sanitizeCustomFontName(settings.uiFontCustom),
    readingFontPreset: normalizePreset(settings.readingFontPreset),
    readingFontCustom: sanitizeCustomFontName(settings.readingFontCustom),
    readingFontSize: clamp(Number(settings.readingFontSize), 13, 19),
    readingLineHeight: clamp(Number(settings.readingLineHeight), 1.4, 2),
  };
}

export function applyZhixuAppearance(
  style: CSSStyleDeclaration,
  rawSettings: ZhixuAppearanceSettings,
): void {
  const settings = normalizeAppearanceSettings(rawSettings);

  style.setProperty(
    "--zhixu-ui-font",
    resolveZhixuFontStack(
      settings.uiFontPreset,
      settings.uiFontCustom,
      "--font-interface",
    ),
  );

  style.setProperty(
    "--zhixu-reading-font",
    resolveZhixuFontStack(
      settings.readingFontPreset,
      settings.readingFontCustom,
      "--font-text",
    ),
  );

  style.setProperty(
    "--zhixu-reading-size",
    `${settings.readingFontSize}px`,
  );

  style.setProperty(
    "--zhixu-reading-line-height",
    String(settings.readingLineHeight),
  );
}

export function clearZhixuAppearance(style: CSSStyleDeclaration): void {
  style.removeProperty("--zhixu-ui-font");
  style.removeProperty("--zhixu-reading-font");
  style.removeProperty("--zhixu-reading-size");
  style.removeProperty("--zhixu-reading-line-height");
}
