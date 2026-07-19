# 知序 UI Guidelines

The binding grid, density and behavior baseline is `UI_V3_SPEC.md`, with five source images under `design/ui-v3-references/`. New production UI classes use the `la-` prefix and shared spacing/radius/duration tokens. The main view is a task workspace; the compact Obsidian sidebar is a summary, never a second full application.

## Character

Quiet, dense and native to Obsidian/macOS. Use Obsidian CSS variables, Lucide icons and the system typeface. Do not copy Copilot code, brand assets or visual identity.

## Tokens

```css
--la-space-1: 4px;
--la-space-2: 8px;
--la-space-3: 12px;
--la-space-4: 16px;
--la-space-5: 20px;
--la-space-6: 24px;
--la-radius-small: 6px;
--la-radius-medium: 8px;
--la-radius-large: 10px;
--la-motion-fast: 140ms;
--la-motion-normal: 180ms;
```

## Layout rules

- The main shell owns a 52px global header and a 188px module navigation column; the module nav compresses to 56px below 900px.
- Do not reproduce the Vault file tree inside the plugin.
- The page root never scrolls. Lists, detail documents, Inspectors, plan columns and message history own their scrolling independently.
- Today is list/detail/stats; Materials is material list/Change Set/processing status; Review is draft list/Markdown/source evidence.
- Assistant settings is closed by default and opens as a 320px drawer.
- Sidebar supports 280–480px and contains summaries, not full workflows.
- Details open as workspace tabs.
- Lists use whitespace and separators; cards are reserved for key grouped states.
- Empty states explain the next useful action.
- Every interactive element has hover, focus-visible, disabled state and an aria-label.
- Keyboard activation is first-class.
- Motion stays between 120–200ms and is disabled by `prefers-reduced-motion`.
- Never hard-code theme colors; use Obsidian variables and Accent Color.
- Theme overrides must stay under `.la-app` or `.la-sidebar`; neutralize host-theme heading/strong colors locally without changing ordinary notes.
- Primary titles and list labels are human-readable. Hashes, absolute paths and artifact/source IDs belong only in collapsed technical details.

## Type scale

- Product title: 18–20px maximum, never a marketing hero.
- Module title: 16–18px.
- Content title: 15–16px.
- Section title: `var(--font-ui-medium)` with medium weight.
- Body: `var(--font-ui-small)`/normal.
- Metadata: `var(--font-ui-smaller)` and `--text-muted`.
