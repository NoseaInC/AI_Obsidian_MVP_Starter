---
type: concept
status: reviewed
domain: 因果推断
mastery: 0
created: "2026-07-19"
updated: "2026-07-19"
source_notes: []
ai_generated: true
reviewed: true
tags:
  - knowledge/concept
  - causal-inference
---

# do-calculus 三规则

## 严谨定义

do-calculus 是 Judea Pearl 提出的一组图变换规则，用于判断包含 $do(\cdot)$ 算子的因果量是否可以从观测分布中识别。给定 DAG $\mathcal{G}$ 和不相交节点集 $X, Y, Z, W$：

**规则 1（观测的增删）**：
若在删去指向 $X$ 的所有边的图 $\mathcal{G}_{\overline{X}}$ 中，$Y$ 和 $Z$ 在给定 $X, W$ 下 d-分离，则：

$$P(Y \mid do(X), Z, W) = P(Y \mid do(X), W)$$

**规则 2（干预与观测的交换）**：
若在删去指向 $X$ 的边且删去从 $Z$ 发出的边的图 $\mathcal{G}_{\overline{X}\underline{Z}}$ 中，$Y$ 和 $Z$ 在给定 $X, W$ 下 d-分离，则：

$$P(Y \mid do(X), do(Z), W) = P(Y \mid do(X), Z, W)$$

**规则 3（干预的增删）**：
若在删去指向 $X$ 和 $Z$ 的边的图 $\mathcal{G}_{\overline{X}, \overline{Z(W)}}$ 中（$Z(W)$ 是 $Z$ 中非 $W$ 祖先的节点），$Y$ 和 $Z$ 在给定 $X, W$ 下 d-分离，则：

$$P(Y \mid do(X), do(Z), W) = P(Y \mid do(X), W)$$

## 适用条件与假设

- 因果 DAG $\mathcal{G}$ 正确指定；
- 所有涉及的变量在观测数据中可测（或通过其他方式可推断）；
- 规则应用的每一步均需通过 d-分离验证。

## 直觉

三条规则本质上说的是同一件事的三种变体——**什么时候可以在因果表达式中添加或删除条件变量**：

| 规则 | 操作 | 直觉 |
|------|------|------|
| 规则 1 | 增删普通条件变量 $Z$ | 如果 $Z$ 和 $Y$ 在干预后独立（d-分离），则 $Z$ 可增可删 |
| 规则 2 | 将 $do(Z)$ 换成条件 $Z$ | 如果干预 $Z$ 不额外提供信息（所有后门已被 $X$ 阻断），$do(Z)$ 降级为条件 |
| 规则 3 | 增删 $do(Z)$ | 如果干预 $Z$ 不改变 $Y$ 的分布（$Z$ 无因果效应通路的条件下），$do(Z)$ 可删 |

do-calculus 的威力在于**完备性**：如果一个因果量在 DAG 中可识别，则存在有限步 do-calculus 推导将其化为仅含观测分布的表达式（Shpitser & Pearl, 2006）。

## 公式或关键推导

三条规则的共同结构（简化表示）：

$$\boxed{P(Y \mid do(X), \star_Z, W) = P(Y \mid do(X), \star'_Z, W)}$$

其中 $\star_Z$ 可表示 $Z$（条件）、$do(Z)$（干预），或完全删除 $Z$。每种变换的合法性由对应的删边图 $\mathcal{G}_{\overline{\cdot}\underline{\cdot}}$ 中的 d-分离条件判定。

**后门调整的推导**（作为规则 2 的应用）：
取 $Z$ 为后门协变量集。在 $\mathcal{G}_{\underline{Z}}$（删去 $Z$ 发出的所有边）中，$Z$ 到 $Y$ 只有后门路径（$Z$ 发出的因果边已删除），而这些路径已被 do(X) 产生的删边 $\mathcal{G}_{\overline{X}}$ d-分离。因此规则 2 允许：

$$P(Y \mid do(X)) = \sum_z P(Y \mid do(X), Z=z) P(Z=z \mid do(X))$$
然后规则 3 （$Z$ 不是 $X$ 的后代）给出 $P(Z \mid do(X)) = P(Z)$；规则 2 给出 $P(Y \mid do(X), Z) = P(Y \mid X, Z)$，从而得到后门调整公式。

## 易混淆概念

- **do-calculus vs 后门/前门准则**：后门准则和前门准则是 do-calculus 的图形化快捷方式——满足准则时可以直接套用调整公式，无需逐步推导 do-calculus。
- **识别 vs 估计**：do-calculus 只解决**识别**问题（因果量能否写成观测量的函数），不解决**估计**问题（如何在有限样本中估计该函数）。
- **do-calculus vs 反事实计算**：do-calculus 处理 $P(Y \mid do(X))$（群体层干预），反事实需要 SCM 的结构方程来回答个体层反事实 $Y_x(u)$。

## 具体例子

**前门准则的 do-calculus 推导**（经典：吸烟 → 焦油 → 肺癌，混杂 U 不可观测）：

$$吸烟(S) \to 焦油(T) \to 肺癌(L),\quad 吸烟 \leftarrow U \to 肺癌$$

1. 链式法则：$P(L \mid do(S)) = \sum_t P(L \mid do(T), do(S)) P(T \mid do(S))$
2. 规则 2 应用于 $L$ 和 $S$（给定 $T$ 和空干预）：$P(L \mid do(T), do(S)) = P(L \mid do(T))$（$S$ 对 $L$ 的效应完全通过 $T$ 中介）
3. 规则 2 应用于 $L$ 和 $T$：$P(L \mid do(T)) = \sum_s P(L \mid T, S=s) P(S=s)$（$S$ 是 $T \to L$ 的后门阻断）
4. 规则 3：$P(T \mid do(S)) = P(T \mid S)$（$S \to T$ 无混淆）

合成：$P(L \mid do(S)) = \sum_t P(T=t \mid S) \sum_s P(L \mid T=t, S=s) P(S=s)$

## 常见错误

1. 以为 do-calculus 需要记三条规则的全部细节——实践中大量使用后门/前门准则和规则 2 即可覆盖大部分场景；
2. 混淆规则 2 的适用条件——必须验证 $\mathcal{G}_{\overline{X}\underline{Z}}$ 中的 d-分离，不仅是原图；
3. 忽略完备性定理的假设——do-calculus 完备性要求 DAG 正确且所有变量离散/线性高斯。

## 复习题

1. do-calculus 三条规则分别在什么图变换下验证 d-分离？
2. 为什么说后门调整是 do-calculus 的特例？请用规则 2 和规则 3 推导。
3. 前门准则和工具变量在 do-calculus 框架下的推导有什么相似之处？

## 来源与页码

- Pearl, J. (2009). *Causality*, Chapter 3.4 (The Rules of do-calculus).
- Pearl, J., Glymour, M., & Jewell, N. P. (2016). *Causal Inference in Statistics: A Primer*, Chapter 4.
- Shpitser, I., & Pearl, J. (2006). "Identification of Joint Interventional Distributions in Recursive Semi-Markovian Causal Models." *UAI 2006*.

## 与其他概念的关联

- [[d-分离]]：do-calculus 每条规则的合法性判定都依赖 d-分离。
- [[后门准则]]：后门调整是 do-calculus 规则 2 + 规则 3 的直接推论。
