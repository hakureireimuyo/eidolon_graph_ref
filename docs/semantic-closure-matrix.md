# Kernel 语义闭包矩阵(Semantic Closure Matrix)

> 状态:审计完成(2026-09-05),可检验断言见
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

## 3. 反向检查(真正的缺陷/限制清单)

### G1(已修复)exec 前端 State 字段静默丢失 — **真实缺口**

`compile_dsl` 的 exec 命名空间下,`__module__` 为 `builtins`,类级注解
字符串求值失败被静默跳过 → 带状态的外部节点经统一入口编译后
`state_defaults` 为空、**不报任何错误**。

修复(2026-09-05,eidolon_dsl.py `_DSLMeta.__new__`):注解求值作用域改为
`@group` 函数的 `__globals__`(真实定义域),模块查表仅作无组类的回退。
合约测试锁定:10 个 primitives 经 exec 前端编译,state 契约逐字段一致。

### R1(冻结限制)DataIn.signal 仅同组绑定

IR 允许 `DataIn.signal` 引用**节点级**任意 SignalIn(NodeType 校验为
节点级);运行期 `settle_control_signals` / `signal_active` 也按节点级解析。
DSL 冻结为仅同组(编译期 DefinitionError)。当前没有任何 primitive 或
测试需要跨组绑定;若未来出现真实需求,改动点 = `generate_ports` 的绑定
校验 + 限定名解析,矩阵本行随之更新。审计断言:
tests/test_readiness_gated.py::test_gated_cross_group_binding_rejected。

### R2(冻结限制)readiness 仅限内置谓词

IR 的 `Readiness` 是开放 Protocol,但 DSL 组限定器只认识
DATA/TRIGGER/ALL/ANY,自定义谓词 → DefinitionError("unsupported
readiness predicate")。**不要**为"看起来更强大"先造 readiness algebra;
放开此限制的前提是内核出现必须由自定义谓词表达的语义。

### R3(结构性限制)handler 共享不可表达

裁定 10 允许跨组共享 handler,DSL 的"一个函数 = 一个组"结构使共享
无法表达(同一函数复用会产生重复组名)。属 DSL 结构的有意收紧,不是
语义缺口;手工构造 NodeType 路径仍可共享。

### R4(已冻结)init / init_defaults / type_name 未文档化能力

三者均可经 DSL 表达并正确进入 IR,但此前无文档无测试(DSL 文档标注
"待办:init 钩子的 DSL 形态")。合约测试已把它们升格为冻结契约;
语义补充到 [graph-node-definition-dsl.md](./graph-node-definition-dsl.md) §2.7。

### R5(冻结的宽松)Signal 内型不做编译期校验

`SignalMarker` 内型应为 `bool`(运行期电平恒为 bool),但编译器不校验
内型——`Annotated[int, SignalMarker()]` 照常编译。冻结当前宽松行为;
收紧需内核裁定(可能破坏依赖宽松注解的外部节点)。

### R6(待内核裁定)`readiness=ALL()` 可复活"永真组"

`@group(readiness=ALL())` + 仅 `cfg: Config` 参数可构造无输入、无触发器、
永真谓词的组——与裁定 9/16("永远 ready"契约禁止借壳)精神存在张力。
当前 DSL 接受;测试冻结现状,待内核裁定收紧(编译期拒绝空谓词组)或
明确合法化(如作为宿主轮询钩子)。

## 4. 结论

- **闭包状态**:10 个 primitives 的完整语义契约经 exec 前端逐字段一致
  (合约测试 10/10);除 G1 外**没有发现 DSL 无法表达的内核必需语义**。
- G1 修复后,正向矩阵全部 ✓ 或 ◐(受限均有明确裁定或冻结理由)。
- **DSL 核心语义具备冻结条件**:不建议新增语法(R1/R2/R3/R6 的放开
  都以内核语义演化为前提,而非以 DSL 能力愿望为前提)。
