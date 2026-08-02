# Writing Policy

所有知识写入遵循固定流程：

```
判断目的
→ 检索已有知识
→ 决定更新还是新建
→ 决定笔记类型
→ 决定目标路径
→ 决定链接
→ 生成 WriteIntent
→ Policy 校验
→ Write Plan
→ Snapshot → Apply → Verify → Undo
```

## 更新 vs 新建

- **已有同一知识主体** → 优先更新现有笔记，不默认新建。
- **只是增加新来源** → 更新现有笔记的 `sources` 列表，不创建重复知识笔记。
- **课程章节中出现值得独立复习的概念** → 新建 Concept，并从章节建立链接。
- **内容只服务当前项目** → 写入 `40-Projects`，不进入通用 Knowledge。

## 类型与目录匹配

| 类型 | 目录 |
|---|---|
| `source` | `10-Sources` |
| `course` / `course-chapter` | `20-Knowledge/Courses` |
| `topic` | `20-Knowledge/Topics` |
| `concept` | `20-Knowledge/Concepts` |
| `project` / `project-note` | `40-Projects` |
| `learning-log` | `30-Learning` |

## 保护规则

- `reviewed` / `core` 笔记默认不能由模型直接改写。
- 发现目标是受保护笔记时，只能拒绝或生成显式更新提案，不得直接 Apply。
- 不得通过降低整个 MOC 的保护状态来更新动态内容。

## 复习单元

- `course-chapter` 必须 `review_unit: false`——长章节不进入 Today 复习。
- `topic` 和 `concept` 才允许 `review_unit: true`。
- 正式复习单元条件：`review_unit = true AND status IN (reviewed, core)`。
