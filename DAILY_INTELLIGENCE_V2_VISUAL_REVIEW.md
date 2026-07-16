# Daily Intelligence V2 Visual Review

参考：`design/daily-learning-intelligence-v2.png`  
实机截图目录：`artifacts/daily-intelligence-v2-screenshots/`

## 真实 Obsidian 验收清单

- [x] 模块导航 + 类别 + 列表 + 详情四段布局
- [x] 类别宽度和选中紫色语义
- [x] 推荐列表密度与可读标题
- [x] 新知识声明和元数据摘要
- [x] 六个详情 Tab 可切换
- [x] 固定底部操作栏
- [x] 页面整体不滚动，三个内容区独立滚动
- [x] Obsidian CSS 变量驱动的深色主题规则
- [x] 1240 / 980 / 700 三档窄屏规则及插件逻辑测试
- [x] 无模型、刷新中、错误状态的结构化分支和测试
- [x] Study Session 真实打开、显示、暂停退出
- [x] 隐私设置、脱敏诊断和确认式清理实现

## 实机截图

- `today-daily-knowledge-light.jpeg`：默认每日新知识、五类导航、列表、详情和右侧统计。
- `today-known-light.jpeg`：已知知识分区。
- `today-sources-light.jpeg`：来源与 B 级验证信息。
- `study-session-light.jpeg`：真实学习会话。

## 差异结论

- P0：无。
- P1：无。参考图中的核心信息架构、紫色新知识语义、六个详情分区和固定操作区均已落地。
- P2：当前实机窗口宽度小于参考图，因此列表和详情更紧凑；响应式规则保持信息完整，不出现横向溢出。
- P3：后续可在积累真实行为数据后增加画像趋势可视化，不影响本轮 UI 与学习闭环。
