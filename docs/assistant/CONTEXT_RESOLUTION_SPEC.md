# Context Resolution V1

## Goal

Resolve deictic phrases such as “这个方法”“这篇”“刚才那个” before intent routing. Resolution is deterministic and bounded; a model may suggest entities but cannot choose filesystem authority.

## Resolution order

1. Explicit entity in the current message.
2. Explicit current attachment/PDF.
3. Current Artifact, Task Thread or recommendation.
4. Active method/concept/topic/material from the conversation.
5. Current Obsidian note and selection.
6. The last eight messages and bounded conversation summary.

Named comparison targets do not steal focus from a deictic subject: “把这个方法和 Bootstrap 比较” keeps the prior method active. Low confidence produces one minimal clarification; it never restarts the whole task.

## Contract

The resolver returns `activeTopic`, `activeConcept`, `activeMethod`, `activeMaterial`, active IDs, current note/selection references, confidence, evidence and a turn-scoped `resolution`. Raw conversation bodies remain in local message files, not SQLite.

## Acceptance

The canonical scenario is:

```text
介绍一下 Delta Method。
把这个方法带上推导整理到 Obsidian 中。
```

The second turn must resolve to Delta Method without re-asking and route to a method note.
