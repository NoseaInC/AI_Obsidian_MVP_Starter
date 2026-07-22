# Obsidian Rendering Spec

Updated: 2026-07-14

All model- or Agent-generated Markdown is rendered with Obsidian `MarkdownRenderer` through one shared TypeScript adapter.

The adapter normalizes `\(...\)` and `\[...\]` into Obsidian math delimiters while preserving code fences, inline code, URLs and existing dollar math. It does not inject raw HTML.

The shared renderer is used for:

- assistant answers;
- generic Artifact summaries;
- tutor results and learning packs;
- review preview and user-edited content;
- proposed note preview.

Rendering is cancellable by component disposal, scoped to `.la-` roots and compatible with light/dark themes. Plain IDs, raw JSON and technical paths remain in collapsed technical details rather than primary content.
