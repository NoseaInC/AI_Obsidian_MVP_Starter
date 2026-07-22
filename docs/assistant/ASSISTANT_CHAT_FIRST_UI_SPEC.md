# Assistant Chat-first UI Specification

Visual baseline: `design/assistant-chat-first-v1.png`.

## Layout

- Main area uses `minmax(560px, 1fr)`; context rail is 300px.
- The toolbar and fixed composer never scroll with message content.
- User messages are compact and right aligned. Assistant messages use normal Markdown typography.
- The Task Thread sits directly below its answer and shows status without raw IDs.
- The Learning Pack uses four compact blocks: outcomes, prerequisites, structure and one quiz preview.
- The context rail contains current context, related reviewed/core notes, recent materials and recommended actions.

## States

- Empty context is explicit, not a blank container.
- Running, completed, partial and failed task states use semantic colors derived from Obsidian variables.
- Technical error codes and payloads remain in collapsed details.
- Provider settings open as an overlay drawer; the conversation width does not collapse.
- Below 1040px the context rail hides; below 720px the result grid becomes two columns.

All selectors stay under `.la-`; controls include labels, focus states and disabled states.

