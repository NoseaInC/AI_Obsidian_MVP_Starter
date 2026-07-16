# Assistant Task Thread Specification

Each user request owns one persisted Task Thread:

```text
检查已有知识
→ 检索可信来源
→ 生成适合当前水平的内容
→ 组织内容与练习
→ 生成可继续修改的成果
```

The Runtime stores thread identity, conversation/message references, intent, status, progress, recovery state and Artifact Group reference. Each step has an ordinal, user-facing label, status, timestamps and an optional internal error code.

Status transitions are `planning → running → completed|partial|failed|cancelled`. A retried request creates a traceable new run; it must not duplicate the visible primary result. Raw model/provider errors are never the default user-facing message.

