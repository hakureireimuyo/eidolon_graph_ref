# 内核重构总结（2026-09-04）

## 概述

按 REFACTOR_IMPLEMENTATION_PLAN 实施的两个高优先级项目与两个中优先级项目
已全部完成并提交（master，提交序 `20b8af0` → `669c01c`）。低优先级项目
「IR 校验独立化」经裁定跳过（与既定内联不变式设计冲突，见下文）。

方案设计文档已归档至 `archive/`（历史记录）；本文档是当前状态说明。

## 交付清单

| 提交 | 项目 | 内容 |
|------|------|------|
| `20b8af0` | 🔴 高 1：事件索引优化 | `PortState.pending_events(list[int])` → `pending_deliveries(list[Delivery])` 直接引用；`consume()` 重写为 O(k)，无反向查表/node-port 比对，返回消费事件 id 序列 |
| `0e96600` | perf：fire 路径热点 | DSL wrapper 直接接管 executor 私有状态快照（每 fire 省一次 deepcopy）；`GraphDefinition.nodes` 防御性 dict 拷贝 → `MappingProxyType` O(1) 只读视图 |
| `d088e39` | 🔴 高 2：DSL 编译管道 | `_compile_group` 拆为三阶段七函数（extract / interpret / generate）；端口表 dict → `PortDeclarations` 数据类；错误消息逐字保留 |
| `c13d924` | 🟡 中 1：端口状态统一 | Data/Signal/Trigger 三套状态机 → 单一 `PortState` = `PortInvariants`（frozen，构造校验，运行时只读）+ `RuntimeFacts` + 投递记录；`shared_invariants` 按值去重共享 |
| `669c01c` | 🟡 中 2：Readiness 协议扩展 | 谓词增加 `explain()` / `requires_port_pending()` / `referenced_ports()`；构建期校验改用协议方法；`EIDOLON_DEBUG=1` 时间线记录失败评估 + console 追踪 |

## 验收数据

### 测试

```
基线 102 → 交付 145（全绿，0.19s）
```

新增测试文件：
- `tests/test_event_indexing.py`（链接不变式 / 扇出独立性 / 1000 目标高扇出）
- `tests/test_dsl_compile_pipeline.py`（21 个阶段级单元测试）
- `tests/test_port_state_unified.py`（不变式校验 / 锁存 / 多态 / 共享）
- `tests/test_readiness_protocol.py`（explain / requires / referenced / 调试记录）

### 性能

```
基准: benchmarks/benchmark_event_indexing.py（每 epoch 均值）

场景 1：高扇出 1 → N
  N=1000:  76.4 ms → 15.9 ms   (4.8×)
场景 2：APPEND 累积 K
  K=1000:  4.9 ms → 5.4 ms     (噪声内持平)
场景 3：长链 D=20
          463 µs → 364 µs      (1.3×)

DSL 编译: 10 原语 57 ms（验收线 < 100 ms）；单节点 0.12 ms
```

说明：方案预期的「< 1ms/epoch」由消费路径 O(n²)→O(n)（约 26ms/epoch 的
消除）+ 两个 fire 路径热点修复共同逼近，但剩余开销（每 fire 的语义性
状态快照 deepcopy、可拷贝性探针等）决定了 15.9ms 的下限。profile 显示
剩余开销平坦分布，继续压榨需要 per-visit 类型/声明缓存层（见遗留事项）。

## 与方案的偏离（裁定）

1. **`event_driven` / `has_value` 是粘性锁存，不是派生属性**
   （中 1 方案草案写「`len(pending_deliveries) > 0` 推导」有误）——
   消费即清空 pending，动态资格与值资格必须跨 epoch 保持；且 `None`
   是合法载荷，`has_value` 不可由 `value is not None` 推导。
   实现为只升不降的锁存字段。

2. **trigger/signal/config「takes no default」校验从死代码激活**
   原实现未给特殊参数携带 `has_default`，该检查永不触发；按方案
   `forbids_default` 语义激活（行为收紧，现有测试与原语库不受影响）。

3. **readiness 失败记录默认关闭（`EIDOLON_DEBUG=1` 开启）**
   方案的无条件记录会淹没确定性时间线——失败评估是正常图运转的一部分
   （唤醒 ≠ ready）。调试模式下记录 `KIND_READINESS_FAILED` 条目含
   `explain()` 全文，`console.render_readiness_trace()` 可视化。

4. **低优先级「IR 校验独立化」跳过（用户裁定 2026-09-04）**
   方案要求把 `NodeType.__post_init__` 校验外置，与既定裁定冲突——
   `node_type.py` docstring 明确：「校验内联进 IR，而非外部校验器……
   任何构造路径都必须先通过本校验」。图级错误收集形式已存在于
   `model/validate.py`；lenient 加载场景暂无消费方（无 `from_dict`）。

## 遗留事项

- **性能缓存层**（未做）：`_visit`/`group_ready`/`effective` 每 fire 重复
  查类型表与端口声明、空输出也构建 set——per-visit 类型/声明缓存可再降
  常数因子，属新一层重构
- **行为收紧确认**：偏离 2 的「takes no default」激活如需回退是一行改动
- **序列化加载**（若未来需要）：`to_dict` 存在、无 `from_dict`；若引入，
  可再评估 lenient 校验策略（低优先级方案的消费方）

## 归档

- `archive/REFACTOR_IMPLEMENTATION_PLAN.md` —— 总体方案（两高优先级并行计划）
- `archive/REFACTOR_DSL_COMPILATION.md` —— 高优先级 1 详细设计
- `archive/REFACTOR_EVENT_INDEXING.md` —— 高优先级 2 详细设计
- `archive/REFACTOR_PORT_STATE_UNIFICATION.md` —— 中优先级 1 详细设计
- `archive/REFACTOR_READINESS_VALIDATION.md` —— 中优先级 2 + 低优先级详细设计
