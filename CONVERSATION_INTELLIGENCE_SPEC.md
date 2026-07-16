# Conversation Intelligence Spec

Updated: 2026-07-14

## Storage and privacy

Raw message bodies live as private JSON files under `90-Local-Only/Agent/Conversations/messages/`. SQLite stores references, roles, timestamps and bounded structured state; it is not the conversation prose store.

Each learning turn may update:

- a versioned bounded summary;
- topic/confusion/claimed-knowledge/prerequisite/interest signals;
- recurrence, message evidence and confidence;
- active topic and per-conversation personalization preference.

A single question never becomes a durable interest by itself, and no signal is treated as confirmed mastery.

## Context selection

Later requests use the latest summary, relevant recent messages, current note, reviewed/core matches and explicit attachments. Full history is not resent on every model call.

## User controls

- Disable or re-enable personalization for one conversation.
- Export one conversation to `90-Local-Only/Agent/Conversations/exports/`.
- Keep summary only after explicit confirmation; raw message files are deleted.
- Delete one conversation after explicit confirmation.
- Clear conversations created in the last seven days or all conversations after explicit confirmation.

Exports and diagnostics contain no API Key or session token. Deleting conversation data does not delete reviewed/core Markdown knowledge.
