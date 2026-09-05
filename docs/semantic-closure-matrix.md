# Kernel 语义闭包矩阵(Semantic Closure Matrix)

> 状态:审计完成 + **DSL core 冻结**(2026-09-05,§4 冻结确认),可检验断言见
> [tests/test_primitives_as_contract.py](../tests/test_primitives_as_contract.py) 与
> [tests/test_readiness_gated.py](../tests/test_readiness_gated.py)
>
> 文档角色:**证明 DSL ↔ IR 双向闭包程度**——哪些 IR 语义 DSL 能完整、
> 无歧义地表达,哪些被有意冻结为限制,哪些是真实缺口。本矩阵是"DSL 语义
> 冻结"的可审阅工件;新增任何 DSL 语法前必须回答:它补的是本矩阵的哪一格。

## 1. 闭包定义

```text
∀ valid NodeType,∃ DSL expression:compile(DSL expression) = NodeType   (完备性)
∀ valid DSL expression,compile(DSL expression) 产生唯一合法 NodeType   (无歧义性)
```

NodeType 是语义 IR(graph-node-protocol.md §1.0):DSL 的职责不是尽可能
表达更多东西,而是完整、无歧义地表达内核**已经定义**的节点语义。

## 2. 正向矩阵(NodeType 字段 → DSL 表达)

| IR 字段 | DSL 表达 | 状态 | 备注 |
|---|---|---|---|
| `NodeType.name` | 类属性 `type_name`(缺省 = 类名) | ✓ 冻结 | 未文档化,已由合约测试锁定(R4) |
| `data_in`(REPLACE) | 普通类型参数 `x: int` | ✓ | |
| `data_in`(APPEND) | `Append[T]` / `Annotated[T, AppendMarker()]` | ✓ | |
| `DataIn.default` | 参数默认值(裁定 3) | ✓ | |
| `DataIn.signal` | `Gated[T, "gate"]` / `Annotated[T, GatedMarker("gate")]` | ✓ 同组限定 | **仅同组绑定**(R1) |
| `trigger_in` | `trigger: Trigger` / `Annotated[T, TriggerMarker()]` / `@group(trigger="name")` | ✓ | `trigger=` 解耦端口名末段 |
| `signal_in` | `sig: Signal` / `Annotated[T, SignalMarker()]` | ✓ | 未绑定 SignalIn 按数据输入(裁定 15) |
| `data_out` | `-> T`(名 = 组名) / `@group(outputs=(...))` | ✓ | 裁定 4/12 |
| `signal_out` | `-> Signal[bool]` / `-> Annotated[T, SignalMarker()]` / `@group(outputs=..., signals=...)` | ✓ | |
| `asset_in` | `Asset[T]` / `Annotated[T, AssetMarker(type)]` | ✓ | 节点级声明,构建期注入 |
| `state_defaults` | `State[T] = v` / `Annotated[T, StateMarker()] = v` | ✓ 已修补 | exec 前端曾静默丢失(G1,已修复) |
| `init_defaults` | 类属性 `init_defaults = {...}` | ✓ 冻结 | 未文档化,已由合约测试锁定(R4) |
| `init` | `@staticmethod def init(ctx)` | ✓ 冻结 | 未文档化,已由合约测试锁定(R4) |
| `groups` | `@group` 函数(每函数一组,裁定 1) | ✓ | 手工 `groups=` 属性编译期拒绝 |
| `Group.name` | 函数名 | ✓ | |
| `Group.handler` | 函数本身 | ✓ | handler 共享(裁定 10)DSL 无法表达(R3) |
| `Group.inputs` | 派生(数据参数 + 未绑定信号) | ✓ | |
| `Group.triggers` | 派生(Trigger 参数 + `trigger=`) | ✓ | |
| `Group.outputs` | 派生(返回注解 + `outputs=`/`signals=`) | ✓ | |
| `Group.defaults` | `@group(defaults={...})` | ✓ | 与参数默认值绝不互转(裁定 3) |
| `Group.readiness` | `@group(readiness=DATA/TRIGGER/ALL/ANY)` | ◐ 受限 | 自定义谓词被 DSL 拒绝(R2) |
| `tags` | `@staticmethod def tags()` | ✓ | 只读声明函数(裁定 2026-08-23) |
| `doc` | `@staticmethod def doc()` | ✓ | 同上 |

## 3. 反向检查(缺口 / 有意限制 / 冻结,三类分账)

分类原则:**"IR 能做而 DSL 不能写"不自动等于缺口**。DSL 公开词汇是
Kernel 能力的一个**有意子集**——凡是"有意子集"的边界,必须在此有明确
理由;只有"语义静默丢失 / 必需语义无法表达"才算真实缺口。

### 真实缺口(已修复,零残留)

**G1(已修复)exec 前端 State 字段静默丢失** — 审计最重要的发现。

`compile_dsl` 的 exec 命名空间下,`__module__` 为 `builtins`,类级注解
字符串求值失败被静默跳过 → 带状态的外部节点经统一入口编译后
`state_defaults` 为空、**不报任何错误**——编译成功、NodeType 合法,但
语义已经丢失(合法但错误的 IR)。仅断言"编译成功"的测试永远暴露不了
这种缺陷;只有逐字段比较最终 NodeType 语义才能发现(即本矩阵的方法
价值)。

修复(2026-09-05,eidolon_dsl.py `_DSLMeta.__new__`):注解求值作用域改为
`@group` 函数的 `__globals__`(真实定义域),模块查表仅作无组类的回退。
合约测试锁定:10 个 primitives 经 exec 前端编译,state 契约逐字段一致。

### 有意限制(intentional limitation,IR ⊃ DSL 词汇)

| # | 限制 | 明确理由 |
|---|---|---|
| R1 | `DataIn.signal` 仅同组绑定(IR 为节点级) | 无 primitive / 测试需要跨组绑定;放开前提 = 内核出现真实跨组源选择需求,改动点 = `generate_ports` 绑定校验 + 限定名解析 |
| R2 | readiness 仅限内置谓词 DATA/TRIGGER/ALL/ANY(IR 为开放 Protocol) | DSL 公开的是 Readiness Protocol 的**标准声明子集**;不为"看起来更强大"先造 readiness algebra;放开前提 = 内核出现必须由自定义谓词表达的语义 |
| R3 | handler 共享不可表达(裁定 10 允许) | "一个函数 = 一个组"是 DSL 结构的有意收紧;共享仍经手工构造 NodeType 路径可用 |

### 冻结确认(已升格为契约)

| # | 项 | 状态 |
|---|---|---|
| R4 | init / init_defaults / type_name 可表达性 | ✓ 合约测试锁定 + DSL 文档 §2.7 |
| R5 | Signal 内型不校验(电平恒 bool) | ✓ 宽松行为冻结;收紧需内核裁定 |
| R6 | `readiness=ALL()` 永真组 | ✓ **已关闭——内核裁定 17(2026-09-05)**:空组一律构建错误,显式 readiness 不豁免 |

## 4. 冻结确认(2026-09-05)

| 检查项 | 结果 |
|---|---|
| ambiguous(DSL 可写但语义未定义) | **0** |
| missing(Kernel 必需语义 DSL 无法表达) | **0**(G1 为实现缺陷,非表达缺陷,已修复) |
| intentional limitation | R1/R2/R3,各有明确理由(§3) |
| 真实缺口 | G1,已修复并锁入合约测试 |
| 测试 | **180 全绿**(含 10/10 primitive 合约 + 34 项审计断言) |
| 结论 | **DSL core = Frozen** |

> **DSL 核心语义自 2026-09-05 起冻结。**此后新增 DSL 语法必须同时
> 证明:(a) 对应的 Kernel semantic capability 已存在;(b) 现有 DSL 无法
> 表达它;(c) 更新本矩阵并附带合约测试。否则不接受——DSL 是 Kernel 的
> 稳定定义语言,不是持续增加功能的语言。
