# Assistant Artifact Group Specification

One Task Thread creates one Artifact Group with one primary Artifact. Group fields include conversation, task, source run, primary Artifact, state and timestamps.

Learning output is a single `learning_pack` containing:

- estimated minutes and domain;
- learning outcomes;
- prerequisites;
- ordered sections;
- exactly one embedded quiz preview;
- source references and verification status.

Artifacts with the same conversation, source run, normalized title, type and version are deduplicated. Follow-up edits create a new version linked to its parent. Older versions remain available for audit but are not repeated as parallel result cards.

Formal knowledge mutations continue to use Change Sets. A Learning Pack is a study result, not reviewed knowledge.

