# 大内核功能资产移植计划（Large Kernel → Small Kernel）

> 状态:规划(2026-08-23)。依据 `kernel/eidolon-graph`(大内核,7834 行源码/124 测试)
> 对照评估——**大内核不值得重构,本包(eidolon_graph_ref)为基础上重新编码**。
> 本文件记录大内核中已验证、但本包尚未实现的特殊实现;每项按小内核规范移植,
> 不复制大内核代码。语义以本包 docs/ 为准,大内核 docs/ 仅作行为参考。

## 0. 移植总则

1. **语义基准不可动摇**:本包 89 个语义测试 + 6 篇裁定文档是冻结基准;
   移植的功能必须通过"语义等价验证"(大内核行为 → 本包语义测试锁定),不得反向修改基准。
2. **架构边界不可突破**:内核核心(model/ + engine/)保持零第三方依赖、不认识具体节点;
   被移植能力中凡是"节点形态"的一律放入 `eidolon_primitives` 或独立能力包。
3. **有规格无实现的先实现规格,无规格的先裁定后实现**;出现新问题走
   "裁定修订 → 文档更新 → 测试翻转"流程。
4. **大内核特殊实现(换实现迁移/causal_id/emit_trigger)若无法在本包语义内
   自洽,记录为"再实现"项,不强行移植。**

## 1. 大内核特殊实现清单(按移植优先级)

### 阶段 A —— 有裁定规格,机械补全(先行)

| # | 功能 | 大内核位置/行数 | 本包规格依据 | 说明 |
|---|---|---|---|---|
| A1 | **Snapshot / Replay** | engine/snapshot.py (185) | graph-assets.md §7 快照裁定 | 只含 Graph 引用+Node State+执行状态+AssetRef;恢复=重新解析当前资产系统,绝不恢复旧资产对象 |
| A2 | **持久化(序列化往返)** | model/serialize.py (280) + version.py (31) | 图模型 to_dict 已存在 | 大内核有 JSON round-trip + 版本闸测试;本包 GraphDefinition/NodeType 已可 to_dict,需补 from_dict + 版本标记 |

### 阶段 B —— 执行/编辑能力移植(核心工作)

| # | 功能 | 大内核位置/行数 | 移植要点 | 说明 |
|---|---|---|---|---|
| B1 | **编辑事务(改图不动事实)** | engine/edit.py (355) | 状态迁移语义需按本包"运行时拓扑不可变"裁定重新设计 | 大内核 `_compute_plan`/`_apply_migration` 思路可参考;**`impl_migrations` 换实现迁移是大内核桩,不移植** |
| B2 | **RNG(每节点独立流)** | engine/rng.py (73) | SplitMix64 + FNV 稳定哈希 | 本包当前无 RNG 消费者;实现可直接移植(纯算法,无耦合) |
| B3 | **因果 trace(causal_id)** | runtime.py `_note`(475) | 本包 Timeline 事件档案已具备基础 | 大内核 causal_id 只有文档无实现;本包用 run+seq+Delivery.consumed_by 表达因果,需裁定是否补结构化因果身份 |
| B4 | **实时调度(start/pause/resume)** | runtime.py (321-458) | 本包 epoch=同步排空模型 | 移植为宿主侧能力(线程 + 暂停闸门),不进入内核核心;确定性语义不受影响 |

### 阶段 C —— 节点资产移植(补全原语库)

| # | 功能 | 大内核位置 | 移植要点 | 说明 |
|---|---|---|---|---|
| C1 | **17 内置逻辑元件** | engine/builtins/ (17 文件) | 全部用 DSL v2 重写为 NodeType | Clock/Counter/Timer/Simulate/Comparator/Threshold/Join/Latch/Multigate/Buffer/Input/Output/Random/Switch/And/Or/Not;本包已有 Buffer/Join/Latch 原语,需合并去重 |
| C2 | **脚本节点** | engine/script.py (234) | 大内核有 `_DSL_NAMES` 注入与 17 测试 | 本包 DSL v2 是正式前端,脚本节点应作为 DSL 扩展;`emit_trigger` 大内核也未实现,列为再实现项 |

### 阶段 D —— 外部能力封装(独立能力包)

| # | 功能 | 大内核位置 | 移植要点 | 说明 |
|---|---|---|---|---|
| D1 | **LLM 节点封装** | nodes/llm/ (285) | 引用独立能力库 eidolon-llm,只做协议包装 | 大内核 LlmBridge 结构性依赖 World 快照格式与 Event 回注——本包需按 `AssetIn/Capability` 注入模型重写,桥接层语义需重新裁定 |

### 再实现项(大内核也残缺,本包不承诺当期实现)

- `impl_migrations` 换实现迁移(大内核桩,edit.py 分支不可达)
- `causal_id` 结构化因果身份(大内核仅文档)
- 脚本节点 `emit_trigger` / request continuation
- 端口缓存策略声明(大内核文档有,代码无)
- 并发语义(本包未定义;保持单线程,并发留给宿主多实例并行)
- 真实 AssetSystem(管理面/使用面分离、失效恢复,asset-protocols.md §9.1 已裁定规格)

## 2. 移植顺序与验收标准

```
阶段 A(Snapshot/持久化) → 阶段 B(编辑/RNG/trace/调度) → 阶段 C(节点资产) → 阶段 D(LLM 封装)
```

- 每阶段验收:对应语义测试全绿 + 大内核对应测试移植为"行为等价"对照(按本包架构重写,非复制)。
- 阶段 A/B 不动节点语义;阶段 C/D 只新增节点类型,不触碰内核核心。
- 移植完成标志:本包可加载大内核测试夹具图并复现大内核 124 测试所锁定的行为
  (编辑预览/headless 运行同一内核)。

## 3. 相关工作区注意

- 本包工作区存在 2026-08-23 未提交同步审查修复时,先提交再开始移植(基准点确认)。
- 大内核 eidolon-graph 保留为对照物,直到本包完成全部行为等价验证;之后按仓库管理决策归档。
