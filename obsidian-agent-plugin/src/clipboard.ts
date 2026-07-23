function clipboardDocument(): Document | null {
  return typeof document === "undefined" ? null : document;
}

function clipboardNavigator(): Navigator | null {
  return typeof navigator === "undefined" ? null : navigator;
}

/**
 * Copy exact source text without logging, persisting, or sending it anywhere.
 * The textarea path keeps copy working in Obsidian/WebViews where the modern
 * Clipboard API is unavailable or temporarily denied.
 */
export async function copyText(text: string): Promise<void> {
  if (!text) throw new Error("clipboard_text_empty");

  const clipboard = clipboardNavigator()?.clipboard;
  if (clipboard?.writeText) {
    try {
      await clipboard.writeText(text);
      return;
    } catch {
      // Fall through to the local DOM copy path.
    }
  }

  const ownerDocument = clipboardDocument();
  if (!ownerDocument?.body || typeof ownerDocument.execCommand !== "function") {
    throw new Error("clipboard_unavailable");
  }

  const textarea = ownerDocument.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.setAttribute("aria-hidden", "true");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  textarea.style.top = "0";
  textarea.style.opacity = "0";
  textarea.style.pointerEvents = "none";
  textarea.style.userSelect = "text";
  textarea.style.setProperty("-webkit-user-select", "text");

  ownerDocument.body.appendChild(textarea);
  try {
    textarea.focus();
    textarea.select();
    if (!ownerDocument.execCommand("copy")) {
      throw new Error("clipboard_copy_rejected");
    }
  } finally {
    textarea.remove();
  }
}

/** Resolve at click time so streaming controls never copy a stale snapshot. */
export async function copyTextFrom(source: () => string): Promise<void> {
  await copyText(source());
}
