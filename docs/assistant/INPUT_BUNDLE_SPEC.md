# Unified Input Bundle V1

Every assistant turn is normalized to an `InputBundle` before routing:

- current message and last eight message references;
- bounded summary and `ConversationFocus`;
- current note path/frontmatter and selected text;
- current or carried-forward authorized attachments;
- URLs and substantial pasted text;
- active Artifact, Task Thread and recommendation;
- user goal, requested output/destination and autonomy mode.

Only the current request process holds raw message/pasted text. Persistent material records store references, hashes, named topics and bounded metadata. Carried attachments preserve “这篇” across turns without re-uploading or enqueuing the same PDF twice.
