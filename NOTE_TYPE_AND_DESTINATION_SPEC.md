# Note Type and Destination V1

| Material / intent | Result | Default destination |
| --- | --- | --- |
| PDF / paper | source note | `10-Sources/Papers/` |
| Public web page | web source note | `10-Sources/Web/` |
| Pasted/class text | source note | `10-Sources/Notes/` |
| AI conversation | conversation source note | `10-Sources/AI-Conversations/` |
| Reusable method | method note | `20-Knowledge/Concepts/` |
| Cross-source synthesis | topic note | `20-Knowledge/Topics/` |
| Ambiguous capture | inbox draft | `01-Inbox/` |
| Explicit current-note update | managed append | current note |

Current note wins only when explicitly requested and relevant. Exact title/alias matches prevent duplicates. Formal notes create update suggestions. Paper-specific modules and local losses remain in source notes unless independently reusable.
