import {App, Component, MarkdownRenderer} from "obsidian";
import {normalizeAssistantMarkdown} from "./markdown-normalize";

export interface AssistantMarkdownRenderer {
  render(container: HTMLElement, markdown: string, sourcePath?: string, owner?: Component): Promise<void>;
}

export class ObsidianAssistantMarkdownRenderer implements AssistantMarkdownRenderer {
  constructor(private app: App, private defaultOwner: Component) {}

  async render(container: HTMLElement, markdown: string, sourcePath = "", owner: Component = this.defaultOwner): Promise<void> {
    container.addClass("markdown-rendered", "la-native-markdown");
    await MarkdownRenderer.render(this.app, normalizeAssistantMarkdown(markdown), container, sourcePath, owner);
  }

  async renderAtomic(
    container: HTMLElement,
    markdown: string,
    sourcePath = "",
    owner: Component = this.defaultOwner,
    shouldCommit: () => boolean = () => true,
  ): Promise<boolean> {
    const staging = container.ownerDocument.createElement("div");
    await this.render(staging, markdown, sourcePath, owner);
    if (!shouldCommit()) return false;
    container.classList.add("markdown-rendered", "la-native-markdown");
    container.replaceChildren(...Array.from(staging.childNodes));
    return true;
  }
}

/**
 * Renders cumulative stream output at a human-readable cadence. Rendering is
 * staged off-DOM and committed atomically, so the user never sees the empty
 * gap caused by clearing a message before Obsidian finishes Markdown parsing.
 */
export class ProgressiveAssistantMarkdown {
  private latest = "";
  private revision = 0;
  private committedRevision = 0;
  private timer = 0;
  private chain: Promise<void> = Promise.resolve();
  private disposed = false;
  private selectionDeferredAt = 0;

  constructor(
    private renderer: ObsidianAssistantMarkdownRenderer,
    private container: HTMLElement,
    private cadenceMs = 90,
    private maxSelectionDeferMs = 500,
  ) {}

  push(markdown: string): void {
    if (this.disposed || markdown === this.latest) return;
    this.latest = markdown;
    this.revision += 1;
    if (this.timer) return;
    this.timer = window.setTimeout(() => {
      this.timer = 0;
      void this.queueLatest();
    }, this.cadenceMs);
  }

  private selectionCommitDelay(): number {
    const selection = this.container.ownerDocument.getSelection?.();
    const anchorInside = Boolean(
      selection?.anchorNode && this.container.contains(selection.anchorNode),
    );
    const focusInside = Boolean(
      selection?.focusNode && this.container.contains(selection.focusNode),
    );
    const selectingInside = Boolean(
      selection
      && !selection.isCollapsed
      && (anchorInside || focusInside),
    );
    if (!selectingInside) {
      this.selectionDeferredAt = 0;
      return 0;
    }
    const now = Date.now();
    if (!this.selectionDeferredAt) this.selectionDeferredAt = now;
    const remaining = this.maxSelectionDeferMs - (now - this.selectionDeferredAt);
    if (remaining <= 0) {
      this.selectionDeferredAt = 0;
      return 0;
    }
    return Math.min(50, remaining);
  }

  private schedule(delay = this.cadenceMs): void {
    if (this.disposed || this.timer) return;
    this.timer = window.setTimeout(() => {
      this.timer = 0;
      void this.queueLatest();
    }, delay);
  }

  private queueLatest(): Promise<void> {
    const revision = this.revision;
    const markdown = this.latest;
    this.chain = this.chain.then(async () => {
      if (this.disposed || revision < this.revision) return;
      const selectionDelay = this.selectionCommitDelay();
      if (selectionDelay) {
        this.schedule(selectionDelay);
        return;
      }
      const committed = await this.renderer.renderAtomic(
        this.container,
        markdown,
        "",
        undefined,
        () => !this.disposed && revision === this.revision,
      );
      if (committed) {
        this.committedRevision = revision;
        this.selectionDeferredAt = 0;
      }
    }).finally(() => {
      if (!this.disposed && this.committedRevision < this.revision && !this.timer) {
        this.schedule(this.selectionCommitDelay() || this.cadenceMs);
      }
    });
    return this.chain;
  }

  async flush(markdown = this.latest): Promise<void> {
    this.push(markdown);
    if (this.timer) { window.clearTimeout(this.timer); this.timer = 0; }
    while (!this.disposed && this.committedRevision < this.revision) {
      await this.queueLatest();
      if (this.committedRevision < this.revision) {
        const delay = this.selectionCommitDelay();
        if (delay) {
          await new Promise<void>(resolve => window.setTimeout(resolve, delay));
        }
        if (this.timer) { window.clearTimeout(this.timer); this.timer = 0; }
      }
    }
    if (this.timer) { window.clearTimeout(this.timer); this.timer = 0; }
  }

  dispose(): void {
    this.disposed = true;
    if (this.timer) window.clearTimeout(this.timer);
    this.timer = 0;
  }
}
