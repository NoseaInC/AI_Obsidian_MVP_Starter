# Material Understanding V1

`MaterialUnderstanding` classifies conversation, PDF, web page, pasted text, text/Markdown file, AI conversation, folder and mixed bundles. It records purpose, named topic, source metadata, relationships, confidence, warnings and suggested result types.

Knowledge units distinguish user statements, questions, AI explanations and source excerpts. User material is `user-provided`; AI explanations remain `needs-verification`; source excerpts retain attachment/source references and PDF page evidence when provided by extraction.

Bodies stay in `90-Local-Only`; SQLite stores unit type, title, provenance and content reference. Unit IDs are scoped to immutable turn bundles so repeated recent messages do not collide.

Default output limits are one source/synthesis note plus at most three high-value knowledge actions. Raw mixed sources are not concatenated into a giant note.
