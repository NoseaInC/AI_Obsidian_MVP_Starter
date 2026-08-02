# Note Types

知序只承认以下笔记类型。所有知识写入必须选择一个类型，类型决定目录归属和复习语义。

| 类型 | 目录 | 说明 |
|---|---|---|
| `source` | `10-Sources` | 外部资料身份和出处，不承载知识正文 |
| `course` | `20-Knowledge/Courses` | 课程级 MOC / 课程总览 |
| `course-chapter` | `20-Knowledge/Courses` | 有明确课程顺序的章节材料 |
| `topic` | `20-Knowledge/Topics` | 围绕一个完整问题的跨来源综合 |
| `concept` | `20-Knowledge/Concepts` | 可独立定义、解释、复习和复用的概念 |
| `project` | `40-Projects` | 项目级入口 |
| `project-note` | `40-Projects` | 只在某个项目语境中成立的笔记 |
| `learning-log` | `30-Learning` | 学习计划、过程和反思 |

## 决策边界

选择类型时按以下顺序判断：

1. **有明确课程顺序** → `course-chapter`（放入对应课程目录）
2. **围绕一个完整问题进行跨来源综合** → `topic`
3. **可以独立定义、解释、复习和复用** → `concept`
4. **只在某个项目语境中成立** → `project-note`
5. **只是外部资料身份和出处** → `source`
6. **只是学习计划、过程和反思** → `learning-log`

## Concept 创建门槛

不要为每个术语创建 Concept。一个概念至少满足以下条件之一：

- 被两个以上笔记复用；
- 值得独立复习；
- 是其他知识的前置概念；
- 可以独立解释和测试；
- 用户明确要求单独沉淀。

不满足门槛的内容应该作为正文的一部分留在所在笔记中，而不是单独建笔记。
