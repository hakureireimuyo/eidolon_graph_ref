# Annotated Marker 语义冻结(2026-09-05)

> 状态:冻结。断言见 tests/test_annotated_markers.py 与
> [tests/test_readiness_gated.py](../tests/test_readiness_gated.py)。
>
> 文档角色:**把 6 个 marker 的语义精确化并冻结**。marker 集合不扩增;
> UI/schema 元数据(Range、Widget、DefaultSchema 等)不属于 DSL 层——
> DSL 的 Annotated 元数据只表达 Kernel 已定义的**图语义角色**。

## 1. 总则

```text
Annotated[T, Marker(...)] = Python 值类型 T(类型检查器可见)
                           + Eidolon 语义角色(编译器读取)
```

- 角色判定只看 marker **类型**,不看 T(内型是文档性注解,不校验;
  例外:AssetMarker 的 asset_type 参与构建期 isinstance 校验)。
- marker 与旧形式(`Trigger` / `Signal[T]` / `Gated[T, "..."]` /
  `Append[T]` / `Asset[T]` / `State[T]`)**语义完全等价**,编译产物
  逐字段一致(primitive 合约测试锁定)。
- 该集合已冻结:新增 marker 必须先回答它表达的是内核哪个既有语义、
  为什么旧形式无法表达(见 semantic-closure-matrix.md)。

## 2. Marker 语义表

| Marker | 位置 | 语义 | 编译产物 |
|---|---|---|---|
| `StateMarker()` | 类字段 | 节点级状态字段,默认值 = 赋值 | `state_defaults[field] = v` |
| `TriggerMarker()` | 参数 | 组激活条件 | `TriggerIn("{group}.{name}")` + 组 triggers |
| `SignalMarker()` | 参数 | 信号依赖(未绑定 = 纯数据输入) | `SignalIn` |
| `SignalMarker()` | 返回值 | 信号输出(单输出裸值形态) | `SignalOut("{group}")` |
| `GatedMarker("gate")` | 参数 | 数据来源选择:数据有效性由 gate 电平参与解释 | `DataIn(signal="{group}.gate")` |
| `AppendMarker()` | 参数 | 累积型输入(增量批次,消费排空) | `DataIn(cache=APPEND)` |
| `AssetMarker(asset_type=T)` | 参数 | 资产依赖(构建期注入,声明即必须) | `AssetIn(name, T)`;省略 type = 不做类型检查 |

旧形式对照:`Trigger` ↔ `TriggerMarker()`;`Signal`(参数)↔
`SignalMarker()`;`Signal[bool]`(返回值)↔ `Annotated[bool, SignalMarker()]`;
`Gated[int, "gate"]` ↔ `Annotated[int, GatedMarker("gate")]`;
`Append[T]` ↔ `Annotated[T, AppendMarker()]`;
`Asset[T]` ↔ `Annotated[T, AssetMarker(asset_type=T)]`;
`State[T]` ↔ `Annotated[T, StateMarker()]`。

## 3. 编译规则

1. **类字段**:仅 `_is_state` 为真的注解成为 state 字段
   (State / `State[T]` / `Annotated[T, StateMarker()]`);其余注解字段
   被忽略(冻结当前行为,含 `Annotated[T, TriggerMarker()]` 字段)。
2. **参数**:`_get_role` 统一判定(Annotated 元数据 → 旧裸类 → 旧
   `_Marker`),产物一致;`Annotated[T, StateMarker()]` 作参数 → 编译期
   DefinitionError(unknown annotation),state 只能声明为类字段。
3. **返回值**:只识别 `SignalMarker`(→ signal_out);`_Marker`
   signal_out 旧形式等价;其余 marker 出现在返回值按普通数据注解处理
   (冻结当前行为)。
4. **GatedMarker.signal 必须为字符串**;指向同组 Signal 参数(1:1)。
5. **AssetMarker 参数禁止默认值**(资产不是数据,不存在 fallback)。
6. **Config 无 marker**:配置通道只经裸 `Config` 参数表达;不存在
   `ConfigMarker`(防止把"配置注入"这一隐式通道伪装成端口语义)。

## 4. 非法用法与错误消息

| 用法 | 错误(DefinitionError) |
|---|---|
| `x: Annotated[int, StateMarker()]` 作参数 | `unknown annotation` |
| `x: Annotated[int, GatedMarker(7)]` | `Gated binding must be a string, got 7` |
| `x: Annotated[int, AssetMarker()] = 1`(带默认值) | `asset parameter 'x' takes no default` |
| `x: Annotated[int, GatedMarker("nope")]`(无此信号参数) | `Gated binding 'nope' must reference a Signal parameter` |
| `self` 作参数 / `this` 带注解 / `this` 非首位 | 既有 DSL 规则,见 graph-node-definition-dsl.md §2.3-2.4 |

## 5. 边界政策(冻结)

- **不向 Annotated 塞入**:schema/validation(Range)、UI 描述(Widget、
  doc 字符串)、编辑器元数据——这些属于描述层(doc/tags)或图配置面,
  不是端口/状态语义。
- **扩增流程**:新 marker 提案必须 (a) 指明对应的内核语义与 IR 字段,
  (b) 说明为何旧形式无法表达,(c) 更新 semantic-closure-matrix.md
  并附带合约测试。
