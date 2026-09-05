# Readiness 与 Gated 语义审计(2026-09-05)

> 状态:审计完成,编译层断言见
> [tests/test_readiness_gated.py](../tests/test_readiness_gated.py)。
>
> 文档角色:把 Gated 的七个模糊点与 Readiness 的引用边界**逐条落到内核
> 裁定**上(graph-group-protocol.md §5/§6、裁定 14/15/16),作为 DSL 编译
> 器行为与错误消息的权威依据。运行期模式规则由既有语义测试冻结,本文档
> 只回答"DSL 该接受什么、拒绝什么、为什么"。

## 1. Gated 到底是什么(审计问题 ⑦)

**Gated = 数据来源选择(源选择),不是 validity 判断,不是执行门控,不是
消费条件。**

| 候选解释 | 裁定 |
|---|---|
| 数据是否"有效" | ✗ 数据事件照常进入、照常缓存,与信号状态无关(§6.2) |
| 组是否"就绪" | ✗ Signal 退出 Readiness(裁定 14) |
| 消费条件 | ✗ 消费按组 fire 统一进行,与 gate 无关 |
| **数据来源选择** | ✓ `DataIn.signal` 决定动态/静态吸收的第三决定因子(§6.1) |

运行期效果(§6.1 模式规则):

```text
signal_active(p) = 无绑定 或 绑定信号未连接 → True
                   已绑定且已连接           → level == HIGH

gate HIGH(动态):DATA 叶需 pending(必须等待),handler 收事件值
gate LOW / 未连接(静态):DATA 叶真空为真(不等待),handler 收
                       config.ports[端口] → DataIn.default
```

## 2. Gated 七问逐条裁定

| # | 问题 | 裁定 | 依据 |
|---|---|---|---|
| ① | gate 必须在同一 group? | **DSL:必须**。编译期拒绝跨组绑定(限定名 `"a.gate"` 同样拒绝) | DSL 冻结限制,矩阵 R1 |
| ② | gate 可以来自另一个 group? | **IR:可以**(节点级校验与运行期解析),**DSL:不可以** | 矩阵 R1;放开前提 = 出现真实需求 |
| ③ | 一个 signal 可以 gate 多个 data? | **不可以**。SignalIn↔DataIn 严格 1:1,编译期 `already gates` | 裁定 15;NodeType 不变式 |
| ④ | 一个 data 可以由多个 signal gate? | **不可以**。`DataIn.signal` 是单值 | 结构决定,无歧义 |
| ⑤ | gate signal 是否必须 bool? | **运行期电平恒为 bool**;内型注解不校验(冻结宽松) | 矩阵 R5 |
| ⑥ | gate 是否影响 readiness? | **间接影响**:动态/静态模式改变 DATA 叶求值(等待 vs 真空为真);SIGNAL 叶条件不存在 | 裁定 14,§6.1 |
| ⑦ | validity 还是 consumption condition? | 两者皆非——**源选择**(见 §1) | §6 |

## 3. DSL 编译层判定表(可执行断言)

| 输入 | 结果 | 错误消息(DefinitionError) |
|---|---|---|
| `Gated[int, "gate"]`,gate 为同组 Signal 参数 | ✓ DataIn(signal="{group}.gate") | — |
| binding 指向数据参数 | ✗ | `Gated binding 'x' must reference a Signal parameter` |
| binding 指向不存在的名字 | ✗ | 同上 |
| binding 指向其他组的 Signal(含限定名 `"a.gate"`) | ✗ | 同上 |
| 同一 Signal 绑定两个 Gated 数据 | ✗ | `signal 'gate' already gates 'x'` |
| binding 非字符串(`Gated[int, 7]`) | ✗ | `Gated binding must be a string, got 7` |
| 未绑定 `sig: Signal` | ✓ 组 inputs 含 `{group}.sig`,按数据输入 | — |
| `-> Signal[bool]` / `-> Annotated[T, SignalMarker()]` | ✓ SignalOut | — |

## 4. Readiness 引用边界

| 输入 | 结果 | 错误消息 |
|---|---|---|
| `readiness=DATA("x")` 且 `x` 为本组数据参数 | ✓ 叶端口限定为 `{group}.x` | — |
| `readiness=TRIGGER("go")` 且 `go` 为本组 Trigger 参数 | ✓ | — |
| 引用本组不存在的端口 `DATA("zzz")` | ✗ | `readiness references non-group port` |
| 引用其他组端口 `DATA("a.x")`(限定名) | ✗ | `readiness references non-group port` |
| 自定义谓词(满足 Readiness 协议但非内置) | ✗ | `unsupported readiness predicate` |
| 缺省(不写 readiness=) | ✓ `Group.readiness = None`,内核推导 ALL(data) ∧ ANY(triggers);DSL **不物化** | — |

## 5. 待内核裁定事项(冻结当前行为)

1. `@group(readiness=ALL())` 的"永真组"(无输入/无触发器,仅 cfg 参数)
   当前可编译——与裁定 9/16 精神存在张力(矩阵 R6)。
2. Signal 内型是否收紧为编译期强制 bool(矩阵 R5)。
