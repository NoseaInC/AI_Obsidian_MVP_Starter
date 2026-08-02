# Linking Policy

正文中使用有语义的 Wikilink，不允许只在文末堆积无语义链接。

## 语义字段

| 字段 | 含义 |
|---|---|
| `parent` | 上级结构（如章节所属的课程 MOC） |
| `course` | 所属课程 |
| `project` | 所属项目 |
| `sources` | 来源笔记列表 |
| `prerequisites` | 前置知识 |
| `related` | 相关概念 |

## 正文内链接

- 在正文相关位置直接写 `[[笔记名]]`，说明"为什么相关"。
- 不要集中在文末复制一份链接清单（无语义堆积）。
- 链接目标不存在时，WriteIntent 必须明确标记为"待创建"。

## Frontmatter 引用

- `sources`、`prerequisites`、`related` 等结构化引用放在 frontmatter 数组中。
- 这些字段是检索和复习的依据，保持整洁。
