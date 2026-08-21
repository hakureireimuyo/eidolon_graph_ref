# 节点协议(内核 ↔ 外部节点 ABI)

> 状态:已裁定 + 已验证(2026-08-21,§11 边界 14 例全部通过;裁定修订:新增 §7 init 构建期初始化钩子)
>
> 定位:本文档锁定节点协议的语义裁定——内核与外部/自定义节点实现之间的
> 唯一契约(ABI)。它是已冻结内核语义的**暴露面**,不是第二套内核。最小
> 验证内核以 `tests/test_node_protocol.py` 验证边界(见 §11)全部通过后,
> 协议随语义基线一并冻结。
>
> 相关:[Asset 模型](./graph-assets.md)、[资产协议与资产管理系统协议](./graph-asset-protocols.md)——
> 节点与资产之间的能力使用协议、资产与资产系统之间的管理协议。

## 1. 背景与定位

内核已冻结的语义(图 / 端口 / 事件 / Readiness / 时间线)描述的是**节点之间
如何形成计算结构**;但节点本身如果只能由内核内部代码实现,内核就没有完成
它与外部世界之间的最后一道边界定义。节点协议就是这道边界:

```text
                外部世界(自定义节点包 / 脚本 / LLM / IO / ...)
                          │
                   ┌──────▼──────┐
                   │ Node Protocol │
                   └──────┬──────┘
                          │
                eidolon_graph_ref 内核
```

关键定位(2026-08-21 裁定):

1. **协议不是节点实现,而是实现与内核之间的 ABI。** 内核不知道也不需要知道:
   节点是不是 Python 写的、是不是 LLM、有没有内部状态机、用了什么第三方库。
   内核只通过协议知道:它是什么节点、声明了什么端口、需要什么配置与资产、
   什么条件下可激活、激活后如何向内核报告结果。
2. **协议是冻结语义的暴露面,不是第二套内核。** 协议元素与内核语义一一映射
   (§2-§5),不发明新概念,不出现"Kernel Signal / Protocol Event"式的翻译层。
   本次唯一代码新增 = §7 的 init 构建期钩子(裁定修订)。
3. **协议规范节点能向内核提出什么请求,不规定节点内部怎么实现。** 本文定义
   的是 graph-asset-protocols.md §23 三层协议中的**执行协议**(Node ↔
   Graph Runtime),与能力使用协议(Node ↔ Asset)、资产管理协议
   (Asset ↔ Asset System)互不替代。

依据:graph-assets.md §1(编辑与运行分离)、graph-asset-protocols.md §23-24。

## 2. Node 定义层

节点对内核的全部自我描述 = `model/node_type.py::NodeType`(frozen dataclass)。
**声明 = 规则,实现 = 代码,二者分离**:声明的每个字段都是内核可判定的规则,
实现(`tick` / `init`)只是绑定的代码。

| 字段 | 声明内容 | 协议意义 |
|---|---|---|
| `name` | 类型标识 | 身份(图中 `NodeSpec.type` 引用它) |
| `data_in` | `DataIn(name, default, cache, qualified)` | 数据输入;cache = REPLACE / APPEND;`qualified=True` 声明资格槽 |
| `data_out` | `DataOut(name)` | 数据输出;执行时写入即投递 |
| `trigger_in` | `TriggerIn(name)` | 激活请求入口(函数调用入口) |
| `signal_in` | `SignalIn(name)` | 节点级资格 `enable`(持续电平门控) |
| `signal_out` | `SignalOut(name)` | 信号输出;**仅信号节点声明**(信号逻辑的唯一所在地) |
| `asset_in` | `AssetIn(name, type)` | 资产依赖声明(§4,资源平面) |
| `state_defaults` | 状态字段表(带默认值) | 实例跨轮事实的唯一存储;提交超出此表的字段 = 违规 |
| `config_defaults` | 配置字段表 | 编辑期覆盖,运行时只读 |
| `groups` | `InputGroup(name, inputs, triggers, policy)` | 输入组 = 函数调用;每组执行时只读本组输入,组间数据经节点状态传递 |
| `tick` | `tick(ctx) -> TickOutput` | 各组处理逻辑(**唯一可重载点**) |
| `init` | `init(ctx) -> dict | None` | 构建期初始化钩子(§7,2026-08-21 裁定修订) |

派生判定:`is_source`(无输入组 → 每 epoch 按声明序播种执行一次,
group="step");`is_signal_node`(声明 `signal_out`)。

**Readiness 策略**(`Policy`,组触发条件 = pending 如何聚合为 Readiness):

| 策略 | 激活条件 |
|---|---|
| `ON_ALL_DATA_READY` | 组内**全部**动态 Data 端口 ready(默认) |
| `ON_ANY_DATA` | **任一**动态 Data 端口 ready |
| `ON_TRIGGER` | 任一 TriggerIn pending(数据条件真空为真) |
| `ON_DATA_AND_TRIGGER` | 数据齐 ready **AND** Trigger pending(显式门控执行) |

**enable 不强制**(2026-08-21 裁定):`validate()` 不要求任何 `SignalIn`;
外部节点可以声明零 SignalIn。未连接的 enable = 结构常量 True(条件恒成立,
非隐式事件)。`PRIMITIVES` 统一声明 `enable` 是**验证原语的惯例,不是
ABI 强制**——启用节点级门控与否由节点声明自行决定。

端口声明的两个通用事实(节点实现不感知,由内核吸收):
- 一个端口一种声明,不存在独立的"静态端口/动态端口"类型——静态(未连接)/
  动态(已连接)是同一端口的两种运行模式,由连接状态决定。
- 扇入禁止:每个(节点, 端口, 槽位)至多一条线;扇出无限。

## 3. Node 运行层

构建期全序(`GraphInstance.build`,graph-assets.md §5):

```text
结构校验(validate:类型存在 / config 键白名单 / 连线 kind / 扇入 / 绑定结构)
    → config 值域探针(Value:可复制)
    → eager 资产解析(逐节点按声明序:绑定 lookup → resolve → isinstance → 注入)
    → init(§7,每节点至多一次,2026-08-21 新增)
    → 实例构造(失败则 BuildReport error,不存在可 run() 的半成品实例)
```

- **config** = `config_defaults ∪ NodeSpec.config` 合并(白名单 =
  配置字段表 ∪ 数据端口名——按端口名覆盖该端口的静态默认值);运行时经
  `ctx.config` 只读。
- **初始 state** = `state_defaults`(或经 §7 init 修订后的初值)。

执行期,每个 epoch 的节点访问完全由内核决定(基类 final 语义,节点不可触碰):

1. **事件资格(Readiness)**:`_group_ready` 按组策略聚合端口状态——动态端口
   `pending` + 资格(`qualified` 且已连接资格槽:资格 pending AND level==HIGH;
   未连接资格槽恒成立)+ 静态端口资格(已连接资格槽需持续 HIGH);`enable`
   门控 level==HIGH(整节点不执行时数据照常接收缓存)。
2. **执行**:满足 Readiness 的组进入执行——构造 `TickContext(group,
   data_in, state=深拷贝, config, assets=浅拷贝)` → 调用 `tick(ctx)`。
   每组每 epoch 至多一次(NodeTurn 预算);反馈环跨轮迭代。
3. **消费**:组执行成功后消费本轮 pending(value / level 保持,等待新事件)。
4. **状态提交**:`TickOutput.state` 增量合并进节点状态;未知字段 →
   KIND_ERROR + 丢弃该字段;不可复制值 → KIND_ERROR + 拒绝该字段。
5. **产出投递**:data_out 写即投递(不写即不投递,无隐式事件);signal_out
   仅信号节点可写(§5)。

**错误约定(执行期)**:`tick` 抛异常 → 记录 KIND_ERROR + `inst.log`,**不产生
任何输出、不消费 pending**;本轮 NodeTurn 已消耗(同 epoch 不重试),下一
epoch 被唤醒后重试。节点可以安全地依赖此约定:失败是"这次执行失败",
不是"这个实例坏了"。

## 4. 资源访问层

四个概念严格分离(2026-08-20 裁定,graph-assets.md §2-3):

| 概念 | 定义 | 层 | 代码 |
|---|---|---|---|
| **声明** | 节点需要什么能力 | NodeType | `AssetIn(name, type)` |
| **绑定** | 本图使用哪个实例 | GraphDefinition | `bind_asset` → `AssetRef(asset_id)` |
| **解析** | 绑定解析成什么对象 | 构建期 | `AssetResolver.resolve(ref)` + `isinstance` 类型检查 |
| **对象** | 节点实际拿到什么 | 运行期 | `ctx.assets[槽名]`(Capability) |

```text
声明:asset_in = AssetIn("llm", LLMCapability)        "需要什么"
绑定:bind_asset(node, slot, asset_id="llm-42")        "使用哪个"
解析:resolve(AssetRef("llm-42")) → capability          "实际是什么"
使用:ctx.assets["llm"].generate(...)                  "怎么用"
```

- **声明即必须**:声明的槽位构建期必须绑定且解析成功,否则 BuildReport
  error;降级由资产系统提供 Null 资产(真实 Capability),内核永不出 None 槽位。
- `ctx.assets` 键集合 = `asset_in` 声明集合;浅拷贝注入(能力对象共享,
  tick 插入不影响节点 store);**只有使用权,没有所有权**
  (graph-asset-protocols.md §13)。
- 资产不产生事件、不参与 Readiness;运行期失效 = tick 内调用抛异常 →
  既有 KIND_ERROR 语义,恢复由资产系统负责(graph-assets.md §6-7)。

## 5. 内核边界层(Node → Kernel)

节点向内核报告结果的唯一通道 = `TickOutput`,内核向节点投递输入的唯一通道 =
端口状态 + `TickContext`。边界的每一条都已冻结:

- **输入投递**:`Delivery` 先于端口状态更新创建并入档——时间线因果序恒为
  deliver → consume;LOW 自消费路径同样先标记本次投递
  (`tests/test_semantics_matrix.py::test_low_self_consume_marks_delivery_consumed`)。数据与信号在调度层面对称:都不拥有
  "触发权",只改变端口状态并唤醒节点。
- **输出提交**:不写即不投递(没有隐式输出事件);写入未声明的 data_out /
  signal_out 端口 → KIND_ERROR;数据节点写 signal_out → KIND_ERROR
  ("数据节点永远不写信号")。
- **值域**:State/Data/Event 载荷的值域 = Value(可复制/可序列化);Capability
  不得进入任何传播/状态平面。内核在三个入口以 deepcopy 探针校验(只校验
  不复制,传输零拷贝):状态提交、数据产出、宿主注入。
- **零拷贝共享约定**:扇出共享载荷引用是锁定内核事实——`ctx.data_in` 中的值
  可能与其他下游端口共享同一 Python 对象,节点必须视为只读、禁止原地修改;
  产出时应构造新对象(TickContext docstring 的 ABI 约定)。
- **错误分层**(graph-assets.md §6):

| 错误 | 层级 | 语义 |
|---|---|---|
| init 失败 / 资产解析失败 / 结构非法 | 构建期 | BuildReport error;不存在可 run() 的实例;不进 KIND_ERROR |
| tick 异常 | 执行期 | KIND_ERROR + 无输出 + pending 保留,下 epoch 重试 |
| 状态/产出值域违规 | 执行期 | KIND_ERROR + 拒绝提交/产出 |

## 6. Activation / Event 执行契约(核心裁定)

**核心裁定:内核无"节点 in-flight"等待状态。** Activation 是节点执行的
入口,Event 是节点重新进入传播平面的出口;二者之间的时间间隔完全属于节点
实现域。所谓 Completion 不是内核的持续等待状态,而是节点产出结果后重新
进入内核的事件——本协议明确排除 Future / Promise 模型。

接缝语义(2026-08-21 裁定):

```text
Kernel
  │ Activation
  ▼
Node execution domain
  │  可能立即产生 Event,也可能经过任意长时间
  ▼
Event
  │  (经注入 / 传播机制)
  ▼
Kernel:作为新的传播输入继续传播
```

这是**事件协议,不是函数调用协议**。call/return 意味着内核把控制权交给
节点并等待归还;Activation/Event 意味着内核发出工作入口、节点在未来某个
时刻产出新事件。内核只关心:**有没有事件?事件沿哪些连接传播?下游激活
条件是否满足?**——不关心节点内部运行多久。

八条不变量(本协议明文):

1. 节点执行时长不属于图传播语义。
2. 内核不等待节点执行。
3. 内核无 in-flight 等待状态。
4. 未产事件的节点只是"尚未产事件"。
5. 后来的事件 = 图传播的新输入。
6. 事件排序由内核传播语义决定,与节点墙钟时长无关。
7. ref 的 inline tick 只是"即时产事件"的确定性实现。
8. 生产执行隔离属于 Executor / Runtime 层。

**执行时长不构成 Graph Event Ordering 的排序依据**(第 6 条展开):A→C、
B→C,A 内部跑 10s、B 内部跑 1ms——协议既不推导 B 先于 A,也不推导 A 先于 B。
只有"事件已进入内核"之后,内核才按自身传播规则(注入序、声明序、投递序、
Readiness)处理它。这一条既保护确定性模型,又不限制未来真实的异步执行器。

由此,"同步"与"异步"不是两种节点语义,而是同一种协议的不同时间表现:
同步 = 节点在同一执行机会内产出事件的特殊情况;异步 = 节点在未来时刻产出
事件。**不引入 async 属性、不分节点种类。**

当前内核内的异步实现形态 = **状态跨激活持久 + 宿主注入重新进入**:节点在
一次 Activation 中只写状态(如"已请求、待完成"凭据)不产事件,当轮传播照常
静止;之后宿主用 `run([Injection(...)])` 把节点"后来"产出的事件注入图,
内核恢复传播并驱动下游——现有注入机制已足以表达"节点未来产事件",内核
在两个 epoch 之间不存在节点等待状态。

执行接缝是**架构概念**(Kernel → inline dispatch → Node),本次不实现任何
NodeExecutor / CompletionHandle / Future / WorkerPool 对象体系。接缝的实现
选择(Inline / ThreadPool / Process / Sandbox)属于 Runtime 层;任何替换实现
必须保持:投递顺序(Delivery 先于端口状态更新)、pending 消费语义、值域、
错误约定(异常 = 无输出 + pending 保留)、每组每 epoch 至多一次(NodeTurn)。

## 7. init 构建期初始化钩子(裁定修订)

定位 = **Node Instance Construction-time initialization hook**,不是游戏引擎
意义的 Start / Awake:`init ≠ runtime lifecycle`、`init ≠ activation`、
`init ≠ event`——它发生在构建阶段,没有运行时事件语义,不参与传播。

```python
# engine/protocol.py
@dataclass
class InitContext:
    config: dict[str, Any]                 # 合并后的配置(config_defaults ∪ spec.config)
    assets: dict[str, Any] = field(default_factory=dict)  # 本节点已解析能力表(§4)

# model/node_type.py
init: Any = None  # init(ctx: InitContext) -> dict | None
```

- **时机**:构建期、资产解析之后、实例构造之前;每节点至多一次;与
  `tick` 的调用契约分离(`InitContext` 独立于 `TickContext`,字段白名单
  `{"config", "assets"}`)。
- **返回语义**:`dict` = 初始状态增量,合并于 `state_defaults`
  (`{**state_defaults, **delta}`);`None` = 无增量,状态保持默认。
  源节点同样允许声明 init。
- **错误形态(构建期,与执行期分层)**:返回未知状态字段(不在
  `state_defaults`)、不可复制值,或 init 抛异常 → BuildReport error、
  `instance is None`——构建期无运行实例,初始状态不满足即结构前提失败,
  与"声明即必须"同一哲学;不进 KIND_ERROR、不进时间线。
- **兼容性**:默认 `None` = 既存行为逐位不变——现有 92 项语义测试与全部
  既有 NodeType 构造不受影响。

## 8. 注册故事

**内核 registry-agnostic。** 节点类型经宿主传入:

```python
types = {**PRIMITIVES, "MyNode": my_node_type}   # 宿主决定全集
result = GraphInstance.build(definition, types, asset_resolver=host_resolver)
```

- `NodeType` 是公开的 frozen dataclass,外部包直接构造即完成"注册"——没有
  内核内注册表、没有装饰器、没有 import 钩子。
- `PRIMITIVES`(primitives/nodes.py)只是验证原语的便利集合,对外部节点
  无任何强制。
- 内核不认识"实现来源"概念:code / script / LLM / 远程——全部归结为一个
  `NodeType` 值。

## 9. ABI 稳定性承诺

外部实现者**可以依赖**(锁定):

- 端口 / 组 / 策略的声明语义与派生判定(§2);
- `tick(ctx) -> TickOutput` 调用形状与 `TickContext` 字段白名单
  `{group, data_in, state, config, assets}`(内核内部访问 = 违规,
  `tests/test_plane_boundaries.py::test_node_has_no_asset_system_handle`);
- Readiness 计算(§3)与错误约定(§3,§5);
- 不写即不投递;值域 = Value;资产使用面(§4);
- 执行时长不属于传播语义、内核无等待状态(§6)。

内核**不承诺**(graph-asset-protocols.md §11,§26):

- 共享资产的调用顺序(属资产系统与编排节点的责任);
- 节点执行时长的任何排序含义(§6);
- 反射绕过信任模型外的行为(与"节点不得原地修改共享载荷"同一信任模型,
  graph-assets.md §2-5)。

## 10. 边界裁定表

| 边界 | 裁定 |
|---|---|
| 资产所有权 | 使用权非所有权:节点不创建 / 不销毁 / 不管理资产;Capability 接口即契约(graph-asset-protocols.md §13) |
| 值域 | State/Data/Event 载荷 = Value(可复制);Capability 不得进入传播/状态平面(2026-08-20,三入口探针校验) |
| 内核内部访问 | 节点可见面 = TickContext 五字段 + InitContext 两字段;无 Asset System 句柄、无内核对象 |
| 调用顺序 | 共享资产调用顺序不构成 Runtime 语义(graph-asset-protocols.md §11,2026-08-20) |
| 执行时长 | 不属于图传播语义;不构成事件排序依据(§6,2026-08-21) |
| enable | 不强制;未连接 = 结构常量 True(§2) |
| 注册 | 内核 registry-agnostic;宿主传 types(§8) |
| 初始化 | 仅构建期 init 一次;无运行时生命周期钩子(§7) |

## 11. 已验证边界(进入主内核前)

以完全外部定义的节点(直接构造 `NodeType`,不经任何内核内部工厂)验证以下
边界。验证结果:**全部通过**(2026-08-21,`tests/test_node_protocol.py`
14 例),本协议随语义基线一并冻结:

1. 外部节点声明 → 连线 → 构建 → 执行 → 输出投递 + 状态提交全链路(§2,3,5);
2. 四种 Readiness 策略各激活一次且仅一次(§2,3);
3. 错误约定:tick 异常 → KIND_ERROR、无输出、pending 保留、下 epoch 重试成功(§3);
4. 资产四概念:声明 → 绑定 → 解析 → 使用(§4);
5. 使用权非所有权:实例销毁不关闭能力;ctx.assets 不泄漏管理面(§10);
6. init 增量合并于 state_defaults(§7);
7. init 可见合并后 config 与已解析 assets(§7);
8. init 不可复制值 → BuildReport error(§7);
9. init 未知状态字段 → BuildReport error(§7);
10. init 抛异常 → BuildReport error,instance is None(§7);
11. init 默认 None = 既存行为逐位不变(§7);
12. 源节点 init 每构建一次,非每 epoch(§2,7);
13. InitContext 字段白名单 `{config, assets}` 且 TickContext 形状未变(§7);
14. Activation / Event 边界:激活不产事件 → 当轮静止;宿主注入"后来"的事件
    → 恢复传播驱动下游;内核在两个 epoch 之间无等待状态(§6)。
