# UI V3 Visual Review

Last updated: 2026-07-13

## Reference comparison

- Shell: the persistent module navigation is the only plugin-level chrome; the redundant global header and the main view's native Obsidian header are removed.
- Density: compact rows, small semantic badges, restrained borders and no marketing-style hero blocks.
- Today: recommendation list, explanation detail and duration/task Inspector are visible at the wide breakpoint.
- Materials: material state list, Change Set detail and processing status Inspector are visible together.
- Review: artifact list, rendered content with fixed review actions and source/evidence Inspector are visible together.
- Plan: future/tomorrow/weekend, weekly, completed and deferred tasks are organized in three columns with a fixed summary.
- Assistant: context, message history and composer have separate ownership; provider drawer is closed by default.
- Theme/responsive: dark theme and compact-nav/narrow-list state were rendered and checked.

## Screenshots

Located in `artifacts/ui-v3-screenshots/`:

- `01-today-light.png`
- `02-today-dark.png`
- `03-materials.png`
- `04-review.png`
- `05-plan.png`
- `06-assistant.png`
- `07-assistant-settings.png`
- `08-today-narrow.png`
- `09-obsidian-live-today.jpeg`
- `10-obsidian-live-assistant-settings.jpeg`
- `11-obsidian-live-review.jpeg`
- `12-alignment-review.png`
- `13-alignment-materials.png`
- `14-obsidian-live-no-arrows.jpeg`
- `15-header-removed.png`
- `16-obsidian-live-header-removed.jpeg`

## Findings

- P0: none.
- P1: none in preview, typecheck, build, offline tests or real Obsidian 1.12.7 validation.
- P2: under 620px, Materials and Review currently prioritize the list; a dedicated mobile detail back-stack can be expanded in a later iteration.
- P3: preview uses text glyphs where production uses Obsidian Lucide icons.

## Real Obsidian findings closed

- The Things theme applied its global strong-text accent inside plugin content. A `.la-app` / `.la-sidebar` scoped override now keeps headings neutral while preserving semantic confidence color.
- Related micro-knowledge exposed a generated source hash. Human-title normalization now shows “Dragonnet 论文整理”.
- Review preview exposed the audit packet header, absolute path and artifact/source IDs. The main document now renders only note Markdown; full audit data remains available in collapsed technical details.
- The final brand label is consistently “知序”.
- Obsidian/Things fixed-height button styles collapsed Materials and Review rows while their children overflowed. The document-card buttons now use content-driven height, normal wrapping and explicit line-height; visual regression rows measure 113–134px with all content inside the border.
- Lucide icon boxes, status badges, filter chips, navigation items and fixed action buttons now use explicit centering rules independent of the host theme.
- The obsolete global strip (`知序 / 当前模块`, global search, refresh and settings) is removed; module-level search, refresh and settings remain at their relevant locations.
- The complete native Obsidian View Header is hidden only for the 知序 main view, removing its title and three-dot split menu without changing normal note views.
- Browser geometry confirmed zero released gap (`workspaceTop === appTop`) and real Obsidian accessibility validation found zero global-search controls, plugin-header settings buttons or native “更多选项” buttons.
