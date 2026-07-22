# Active Conversation Focus V1

`conversation_focus` is the recoverable pointer layer for long-running assistant conversations.

Persisted fields include active topic/concept/method/material, attachment IDs, Artifact/Task/recommendation IDs, current Vault note, selection reference, last intent/write target, confidence and bounded evidence. Full messages, pasted bodies and secrets are excluded.

Focus changes only when there is explicit evidence. Pronouns prefer the existing subject; comparison objects and incidental named entities do not silently replace it. Deleted attachments are ignored on reconstruction. Conversation deletion cascades to focus.

The UI exposes the active entity and confidence as “当前理解”. An unresolved pronoun is represented as evidence and produces a single compact clarification control.
