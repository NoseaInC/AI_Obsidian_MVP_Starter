# Learning Agent UI Reference Analysis

Reference: `design/learning-agent-ui-reference.png` (1586 × 992).

## Layout

- Obsidian owns the left file browser; the plugin never duplicates it.
- Main workspace uses a 48 px header, 44 px tab bar, then a two-column split: recommendation list 330–390 px and flexible detail 430–760 px.
- The right native sidebar is 280–400 px and contains only status, daily totals, workflow counts, the current job and a compact composer.
- List and detail scroll independently. At widths below 820 px, detail replaces the list; below 560 px, secondary metadata collapses.

## Visual hierarchy

- UI text uses Obsidian/system type: 18 px product title, 15–16 px item titles, 13–14 px body, 11–12 px metadata.
- 1 px theme-derived borders separate regions. Radius is 5/7/9 px; shadows are limited to popovers.
- Selected rows use an accent border and subtle accent background. Normal rows use whitespace and a quiet hover fill.
- Semantic accents: review green, learn blue, explore purple, source orange; all derive from theme variables.
- Primary actions appear once at the bottom of detail. Secondary actions remain neutral.

## Interaction states

- Explicit loading, empty, error, selected, disabled and focus-visible states.
- Search/filter/sort live in the list toolbar. Arrow keys change selection; Enter opens/starts; Cmd/Ctrl+F focuses search.
- Technical identifiers and filesystem paths are hidden behind a disclosure.
- Applied bundles never appear as pending. Failed jobs are separate from active jobs and expose reason/retry.

