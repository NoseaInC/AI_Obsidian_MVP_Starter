# Agent Brain V1 Visual Review

Last updated: 2026-07-13

Status: passed. No known visual P0/P1.

## Evidence

Deterministic screenshots are stored in `artifacts/agent-brain-v1-screenshots/`. The numbered set covers every state required by the Agent Brain V1 specification:

1. Assistant default
2. Assistant source search
3. Assistant capture
4. Brain Run executing
5. Capture Proposal
6. Research Bundle
7. Change Set confirmation
8. Today AI completion
9. Today without a model
10. Materials Research Bundle
11. Review Capture Draft
12. Plan AI task
13. Diagnostics
14. Structured error
15. Loading
16. Empty
17. Dark
18. Light
19. Narrow
20. Narrow Stats Inspector Drawer

The same directory also contains `assistant-provider-drawer-light.png`, the five primary page captures and `obsidian-live-today.png`. The latter is the installed plugin running in Obsidian; the numbered images are reproducible local previews using the production stylesheet and component contracts without network or model access.

## Review result

- No duplicated application/header title; Obsidian's redundant native main-view header and old global strip remain hidden.
- Header height, filters, segmented modes, buttons and cards use the V3 compact scale. List density matches the reference direction.
- Page roots do not scroll. List, detail, Inspector and provider regions own their scrolling; Assistant composer and Review action bar remain fixed.
- Brain Run steps, Proposal status, source/page evidence and the single primary action are visually explicit.
- Technical IDs stay out of primary surfaces and remain behind technical details.
- Error copy states what was rejected and confirms that no write occurred. Loading, disabled, empty and no-model states provide a next action.
- Today keeps the three-column layout at wide width. At narrow width it becomes a single-list surface; the Stats Inspector opens as a right Drawer.
- Provider Drawer exposes Key reference, organization ID, protected custom headers, capability flags and task routing. It never renders a real key.
- Light, dark, Accent Color derivation, focus-visible, hover and reduced-motion rules use scoped Obsidian variables.
- Icon/text controls and list-card interiors use explicit flex/grid alignment; no host-theme fixed-height regression was observed.

## Real Obsidian check

The installed build was reloaded in Obsidian 1.12.7. The compact sidebar reported `在线 · 协议 v1`; Today, Materials, Review, Plan and Assistant loaded current data. Assistant exposed the four task modes and model drawer; Review rendered Markdown and source pages; no obsolete back/forward, refresh, settings or split-menu strip remained.

The desktop screenshot API cached some non-Today frames, so those cached images were not used as evidence for other pages. Reproducible visual-preview captures provide those page-specific records instead.
