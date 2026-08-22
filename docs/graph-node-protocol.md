# 节点协议(内核 ↔ 外部节点 ABI)

> 状态:裁定修订(2026-08-22,Group-centric 迁移),实现与测试翻转待执行
>
> 定位:本文档是节点协议的**最终规范文档**——内核与外部/自定义节点实现之间的
> 唯一契约(ABI)。Group-centric 修订的修正方案与裁定过程记录于
> [graph-group-protocol.md](./graph-group-protocol.md)。
>
> 本次修订:**§2 节点定义层、§3 运行层、§5 内核边界层、§9-§11 重写**;
> §4 资源访问层、§6 Activation/Event 契约、§7 init 钩子语义不变(仅术语更新)。
>
> 相关:[Asset 模型](./graph-assets.md)、[资产协议](./graph-asset-protocols.md)、
> 端口行为参照 [eidolon-graph/docs/graph-ports-bindings.md](../eidolon-graph/docs/graph-ports-bindings.md)。

## 1. 背景与定位

内核语义(图 / 端口 / 事件 / Readiness / 时间线)描述节点之间如何形成计算
结构;节点协议定义内核与外部世界之间的最后一道边界。2026-08-22 修订后,
这道边界的核心概念从"节点"下移到"组":

```text
                外部世界(自定义节点包 / 脚本 / LLM / IO / ...)
                          │
                   ┌──────▼──────┐
                   │ Node Protocol │
                   └──────┬──────┘
                          │
                eidolon_graph_ref 内核
```

关键定位:

1. **协议不是节点实现,而是实现与内核之间的 ABI。** 内核不知道也不需要知道
   节点是不是 Python 写的、是不是 LLM、有没有内部状态机——只通过协议知道:
   它声明了什么端口、需要什么配置与资产、什么条件下执行哪个行为、执行后
   如何报告结果。
2. **协议是冻结语义的暴露面,不是第二套内核。** 协议元素与内核语义一一映射,
   不发明新概念,不出现翻译层。
3. **Node 是容器,Group 是执行单位。** Node 承载身份 / 状态 / 配置 / 端口 /
   资产 / 多个调用契约(Group);真正参与执行语义的是 Group。Node 本身没有
   执行语义。
4. **事件传播层与行为层正交。** 事件系统 Port-centric(Event → Wire →
   Port);Port 经声明层静态归属一个 Group;Event 不携带任何 Group 信息。

## 2. Node 定义层

### 2.1 NodeType

节点对内核的全部自我描述 = `model/node_type.py::NodeType`(frozen dataclass)。
**声明 = 规则,实现 = 代码,二者分离**。

```python
@dataclass(frozen=True)
class NodeType:
    name: str
    # ---- 连接 ABI:端口层(节点级清单,无行为授权)----
    data_in: tuple[DataIn, ...] = ()
    data_out: tuple[DataOut, ...] = ()
    trigger_in: tuple[TriggerIn, ...] = ()
    signal_in: tuple[SignalIn, ...] = ()
    signal_out: tuple[SignalOut, ...] = ()
    asset_in: tuple[AssetIn, ...] = ()
    # ---- 事实 ----
    state_defaults: dict[str, Any] = field(default_factory=dict)
    # ---- 构建配置(仅 init 可见,与行为参数无关)----
    init_defaults: dict[str, Any] = field(default_factory=dict)
    # ---- 行为 ABI:调用契约 ----
    groups: tuple[Group, ...] = ()
    # ---- 描述层(执行路径禁止读取)----
    tags: tuple[str, ...] = ()
    # ---- 构建期钩子(§7)----
    init: Any = None    # init(ctx: InitContext) -> dict | None
```

| 字段 | 声明内容 | 协议意义 |
|---|---|---|
| `name` | 类型标识 | 身份(图中 `NodeSpec.type` 引用它) |
| `data_in` | `DataIn(name, default, cache, signal)` | 数据输入;cache = REPLACE / APPEND;`signal` = 绑定 SignalIn(§2.5) |
| `data_out` | `DataOut(name)` | 数据输出端口清单(连线目标);行为授权归 `Group.outputs` |
| `trigger_in` | `TriggerIn(name)` | 激活请求入口(函数调用入口) |
| `signal_in` | `SignalIn(name)` | 信号输入:绑定数据端口的控制器(§2.5)或纯数据输入 |
| `signal_out` | `SignalOut(name)` | 信号输出;与 data_out 自由组合,写必须声明 |
| `asset_in` | `AssetIn(name, type)` | 资产依赖声明(§4,资源平面) |
| `state_defaults` | 状态字段表(带默认值) | 实例跨轮事实的唯一存储;提交超出此表的字段 = 违规 |
| `init_defaults` | 构建配置默认值 | 仅 §7 init 可见;不参与 Group 行为参数 |
| `groups` | `Group(name, inputs, triggers, outputs, defaults, handler, readiness)` | 调用契约集合(§2.2) |
| `tags` | 角色标签(如 `"source"`) | **纯描述层**:分类/编辑器用;内核执行路径禁止读取;标签不得成为隐式行为开关 |
| `init` | `init(ctx) -> dict | None` | 构建期初始化钩子(§7) |

删除字段:`tick`(行为下移到 Group.handler)、`config_defaults`(拆为
`init_defaults` + `Group.defaults`)、`is_source`(废除源概念)。

### 2.2 Group(调用契约 = 执行单位)

```python
@dataclass(frozen=True)
class Group:
    name: str
    inputs: tuple[str, ...] = ()        # 读哪些 DataIn / 未绑定 SignalIn(纯数据输入)
    triggers: tuple[str, ...] = ()      # 读哪些 TriggerIn;非空 = 给默认策略加 Trigger 门
    outputs: tuple[str, ...] = ()       # 输出授权集合(⊆ data_out ∪ signal_out 名)
    defaults: dict[str, Any] = field(default_factory=dict)  # 行为参数默认值(定义层)
    handler: Any = None                 # handler(ctx: GroupContext) -> GroupOutput | None(必填)
    readiness: Readiness | None = None  # 显式谓词;None = 默认推导(§2.4)
```

- **组 = 一次可触发行为的基本接口**:输入条件 + 触发条件 + 执行行为 + 输出
  授权构成一个完整调用契约;输入组与输出组 1:1(输出组身份 = 组身份,
  `outputs` 即该契约的输出授权集合,不实体化)。
- **端口分区**:每个输入端口在声明层**唯一归属一个 Group**。pending 属于
  Port(端口级事实)因此天然组局部;事件系统不感知 Group。
- **handler 必填**;同一函数可绑定多个组(共享允许),但 Group 身份来自
  Group 本身而非 callable 身份——handler 禁止依赖 `ctx.group` 分发。
- **默认空 Group 报错**:inputs/triggers 皆空且无显式 readiness = 构建错误
  (防止"永远 ready"的自动执行契约)。
- 组间数据经节点状态传递;执行时只读本组输入。

### 2.3 端口声明

```python
@dataclass(frozen=True)
class DataIn:
    name: str
    default: Any = None
    cache: str = REPLACE
    signal: str | None = None   # 绑定 SignalIn 端口名(§2.5);None = 无绑定
```

- DataOut / TriggerIn / SignalIn / SignalOut / AssetIn 形态不变
- **删除**:`DataIn.qualified` 与资格槽(slot qual)——资格槽机制整体撤销,
  由 Signal 绑定(§2.5)取代
- 端口语义参照 graph-ports-bindings.md:一个端口一种声明;静态(未连接)/
  动态(已连接)是同一端口的两种运行模式,由连接状态决定,由内核吸收;
  扇入禁止(每(节点,端口,槽位)至多一条线),扇出无限。

### 2.4 Readiness

```python
# model/readiness.py
ALL(*conds) / ANY(*conds)   # 可嵌套;空集:ALL()=True, ANY()=False
DATA(port)      # 动态端口:pending;静态端口:真空为真(§2.5 模式规则)
                # 对未绑定 SignalIn 输入同样适用(pending,数据化处理)
TRIGGER(port)   # pending(Data Event = 载荷 + 激活;Signal Event = 纯激活)
```

默认推导:

```python
default_readiness(g) =
    ALL(DATA(p) for p in g.inputs)
    ∧ (ANY(TRIGGER(t) for t in g.triggers)   若 g.triggers 非空)
```

- 默认 = 数据齐集自动处理;**声明 triggers = 给默认策略加门**(Trigger 门
  是条件增补,triggers 为空时不存在该层)
- 旧四策略全部是 DSL 实例:

| 旧 Policy | 新表达 |
|---|---|
| ON_ALL_DATA_READY | 默认(仅 inputs) |
| ON_ANY_DATA | `ANY(DATA("a"), DATA("b"))` |
| ON_TRIGGER | `TRIGGER("flush")` |
| ON_DATA_AND_TRIGGER | 默认(inputs + triggers)或显式 `ALL(DATA("a"), TRIGGER("go"))` |

- **Signal 不进入谓词**——信号不是执行调度条件(§2.5)。

### 2.5 Signal 双语义

信号具备两种语义:

**语义一:绑定控制(数据来源选择)。** `DataIn.signal = "gate"` 声明绑定,
严格一对一(每个 SignalIn 至多被一个 DataIn 绑定)。Signal 控制该数据端口
**执行时从哪里取得数据**,不参与"Group 什么时候执行"的判断:

```text
signal_active(p) =
    无绑定 或 绑定信号未连接   → True(无控制器,常规静态/动态)
    已绑定且已连接             → level == HIGH
    (level None = 未激活 = False:未激活时默认数据有效)

动态模式 ⇔ (连接数据线 或 曾注入) ∧ signal_active(p)
```

| 信号状态 | 数据端口模式 | DATA 叶求值 | 执行时 effective argument |
|---|---|---|---|
| 未激活(LOW / None / 未连接) | 静态 | 真空为真(**不等待**) | `config.ports[p]` → `DataIn.default` |
| 激活(HIGH) | 动态 | 需 pending(**必须等待**) | 缓存值(无动态数据回落默认) |

- 数据事件照常进入、照常缓存(与信号状态无关);fire 时统一消费本组端口
  pending,value/level 保持
- 信号事件:level + pending 更新 → 唤醒节点 → 访问时消费 pending(仅触发
  重估);电平翻转即刻改变后续解析,无任何门控语义
- 推论:旧"静态端口 + 资格槽"(受控默认参数)模式死亡——受控输入必须连线
- 与 graph-ports-bindings.md 的关系:§2.2 静态/动态吸收沿用并扩展(信号
  成为第三决定因子);§4 资格门控撤销,由源选择取代

**语义二:纯数据输入。** 信号不绑定数据端口时,与数据同样处理——都是事件
携带的数据。组经 `inputs` 引用未绑定 SignalIn:`DATA(端口)` 按 pending
聚合(与数据输入一致),handler 经 `ctx.data_in[端口名]` 读 level。未绑定
SignalIn 可以独立存在(纯信号节点的形态);观察 = 接收并立即输出类似事件,
吸收 = 接收不产出,皆为普通节点形态,无需端口类别。

### 2.6 实例配置三节与资源三层

```text
Asset    = Node 成员环境(节点级、所有组共享、不在覆盖链上)      → ctx.assets
Config   = 实例级行为参数覆盖(加载期可改、运行期只读)           → NodeSpec.config
Default  = 定义层默认参数                                       → Group.defaults / DataIn.default / init_defaults
```

```python
config = {
    "groups": { "<组名>": { "<参数>": 覆盖值 } },   # Group.defaults 的实例级覆盖
    "ports":  { "<端口名>": 覆盖值 },               # DataIn.default 的实例级覆盖
    "init":   { "<参数>": 覆盖值 },                 # init_defaults 的实例级覆盖
}
```

三条合并链(生命周期严格分离):

```text
init_effective     = {**init_defaults, **config["init"]}                → InitContext.config(构建期一次性)
group_effective(g) = {**g.defaults, **config["groups"].get(g.name, {})} → GroupContext.config(每执行一次只读)
port_static(p)     = config["ports"].get(p.name, p.default)             → 静态模式初始值
```

- 校验白名单:三节的键分别 ⊆ init_defaults 键 / 各组 defaults 键 / data_in
  端口名;未知节/键/组名 = 错误
- **不在 ABI 层要求 handler 参数化**(不搞 Python introspection 注入);
  handler 如何取值是实现细节——函数类比是理解工具,不反向约束 ABI

### 2.7 派生判定与 tags

- `is_signal_node`:声明 `signal_out` 的纯派生观察,不参与执行约束
- `is_source` **删除**:内核不认识"源";源语义由宿主注入表达
- `tags`("source" 等)是描述层分类:编辑器可据此分类/筛选/图分析,内核
  执行路径禁止读取;若某标签需要改变运行时语义,说明该语义属于正式协议

## 3. Node 运行层

### 3.1 构建管线

```text
结构校验(validate:类型存在 / config 三节白名单 / 连线 kind / 扇入 / 绑定结构)
    → config 值域探针(三节递归,Value:可复制)
    → 声明校验(§2.2-§2.5:端口归属分区、handler 非空、readiness 引用、
      空组、signal 绑定 1:1)
    → eager 资产解析(逐节点按声明序:绑定 lookup → resolve → isinstance → 注入)
    → init(§7,config = init_effective,每节点至多一次)
    → 实例构造(失败则 BuildReport error,不存在可 run() 的半成品实例)
```

### 3.2 epoch 与 fire

```text
run(injections):
    注入按注入序入队 → worklist 脏传播(投递唤醒、深度优先)
    → 节点访问 = 按组声明序遍历组:谓词求值 → ready 即 fire
    → 每组每 epoch 至多一次(NodeTurn 预算,(节点, 组))
    → 队列排空即静止
```

**无播种、无源扫描**:`run([])` = 立即静止;宿主节奏经 Injection 表达,
内核不区分事件来源(Source 类节点由宿主注入系统事件驱动)。

fire 流程:

```text
谓词满足
  → 逐端口模式判定 + effective 解析(§2.5)
  → GroupContext 构造(state 深拷贝 / config = group_effective / assets 浅拷贝)
  → group.handler(ctx) -> GroupOutput | None
  → 消费本组端口 pending(value/level 保持)
  → 状态提交(增量;未知字段/不可复制 = KIND_ERROR)
  → 输出校验(键 ⊆ group.outputs,违者 KIND_ERROR + 丢弃)
  → 产出即时投递(零拷贝探针)
```

### 3.3 错误约定

| 错误 | 层级 | 语义 |
|---|---|---|
| init 失败 / 资产解析失败 / 结构非法 / 声明非法 | 构建期 | BuildReport error;不存在可 run() 的实例;不进 KIND_ERROR |
| handler 异常 | 执行期 | KIND_ERROR + 无输出 + pending 保留,下 epoch 重试 |
| 状态/产出值域违规 | 执行期 | KIND_ERROR + 拒绝提交/产出 |
| 未声明输出(∉ group.outputs) | 执行期 | KIND_ERROR + 丢弃该输出 |

保留(冻结语义):NodeTurn 预算、值域三入口探针(状态提交/数据产出/宿主
注入)、零拷贝共享约定(扇出共享载荷引用,节点禁止原地修改输入)、投递
深度优先、反馈环跨轮迭代、静态/动态吸收。

## 4. 资源访问层

四个概念严格分离(不变,graph-assets.md §2-3):

| 概念 | 定义 | 层 | 代码 |
|---|---|---|---|
| **声明** | 节点需要什么能力 | NodeType | `AssetIn(name, type)` |
| **绑定** | 本图使用哪个实例 | GraphDefinition | `bind_asset` → `AssetRef(asset_id)` |
| **解析** | 绑定解析成什么对象 | 构建期 | `AssetResolver.resolve(ref)` + `isinstance` |
| **对象** | 节点实际拿到什么 | 运行期 | `ctx.assets[槽名]`(Capability) |

- **声明即必须**:声明的槽位构建期必须绑定且解析成功,否则 BuildReport
  error;降级由资产系统提供 Null 资产,内核永不出 None 槽位。
- `ctx.assets` 键集合 = `asset_in` 声明集合;浅拷贝注入;**节点级共享,不按
  组切分**(Asset 是 Node 成员环境,不是某次调用的参数);只有使用权,
  没有所有权。
- 资产不产生事件、不参与 Readiness;运行期失效 = handler 内调用抛异常 →
  既有 KIND_ERROR 语义。

## 5. 内核边界层(Node → Kernel)

节点向内核报告结果的唯一通道 = `GroupOutput`,内核向节点投递输入的唯一
通道 = 端口状态 + `GroupContext`。边界的每一条都已冻结:

- **输入投递**:`Delivery` 先于端口状态更新创建并入档——时间线因果序恒为
  deliver → consume。数据与信号在调度层面对称:都不拥有"触发权",只改变
  端口状态并唤醒节点;Readiness 决定是否执行。
- **输出提交**:不写即不投递(没有隐式输出事件);**写必须属于本组**
  ——`GroupOutput.data_out/signal_out` 键 ⊆ `group.outputs`,产出未声明
  端口 → KIND_ERROR;data/signal 对称,自由组合声明。
- **值域**:State/Data/Event 载荷的值域 = Value(可复制/可序列化);Capability
  不得进入任何传播/状态平面。三入口 deepcopy 探针校验(只校验不复制)。
- **零拷贝共享约定**:`ctx.data_in` 中的值可能与其他下游端口共享同一 Python
  对象——必须视为只读、禁止原地修改;产出时应构造新对象。
- **tags 隔离**:内核执行路径不读取 `NodeType.tags`;标签只是描述层分类。

```python
@dataclass
class GroupContext:
    group: str                # 组名(信息性;handler 已绑定组,禁止依赖其分发)
    data_in: dict[str, Any]   # 组内 inputs effective 值 + Trigger 载荷;
                              # 信号输入 = 当前 level
    state: dict[str, Any]     # 当前状态深拷贝
    config: dict[str, Any]    # 本组 effective config(§2.6)
    assets: dict[str, Any]    # 本节点资产能力表(浅拷贝)

@dataclass
class GroupOutput:
    data_out: dict[str, Any] = ...    # 输出端口名 → 值(不写即不投递)
    signal_out: dict[str, bool] = ... # 电平输出;与 data_out 自由组合,写必须属于本组
    state: dict[str, Any] = ...       # 状态变更字段增量

@dataclass
class InitContext:
    config: dict[str, Any]    # init_effective(§2.6)
    assets: dict[str, Any] = ...
```

## 6. Activation / Event 执行契约(核心裁定)

**核心裁定:内核无"节点 in-flight"等待状态。** Activation 是节点执行的
入口,Event 是节点重新进入传播平面的出口;二者之间的时间间隔完全属于节点
实现域。本协议明确排除 Future / Promise 模型。

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

这是**事件协议,不是函数调用协议**。内核只关心:有没有事件?事件沿哪些
连接传播?下游激活条件是否满足?——不关心节点内部运行多久。

八条不变量(本协议明文):

1. 节点执行时长不属于图传播语义。
2. 内核不等待节点执行。
3. 内核无 in-flight 等待状态。
4. 未产事件的节点只是"尚未产事件"。
5. 后来的事件 = 图传播的新输入。
6. 事件排序由内核传播语义决定,与节点墙钟时长无关。
7. ref 的 inline handler 只是"即时产事件"的确定性实现。
8. 生产执行隔离属于 Executor / Runtime 层。

"同步"与"异步"不是两种节点语义,而是同一种协议的不同时间表现。当前
内核内的异步实现形态 = 状态跨激活持久 + 宿主注入重新进入(现有注入机制
已足以表达"节点未来产事件")。执行接缝是**架构概念**,本次不实现任何
NodeExecutor / CompletionHandle / Future / WorkerPool 对象体系。

## 7. init 构建期初始化钩子

定位 = **Node Instance Construction-time initialization hook**:`init ≠
runtime lifecycle`、`init ≠ activation`、`init ≠ event`——发生在构建阶段,
没有运行时事件语义,不参与传播。

```python
# engine/protocol.py
@dataclass
class InitContext:
    config: dict[str, Any]                 # init_effective = init_defaults ⊕ config["init"]
    assets: dict[str, Any] = field(default_factory=dict)  # 本节点已解析能力表(§4)
```

- **时机**:构建期、资产解析之后、实例构造之前;每节点至多一次;与
  `handler` 的调用契约分离(`InitContext` 独立于 `GroupContext`)。
- **返回语义**:`dict` = 初始状态增量,合并于 `state_defaults`;
  `None` = 无增量。
- **错误形态(构建期,与执行期分层)**:返回未知状态字段、不可复制值,
  或 init 抛异常 → BuildReport error、`instance is None`。
- **兼容性**:默认 `None` = 无行为变化。
- `init_defaults` 仅 init 可见,不参与 Group 行为参数覆盖——构建 Node 与
  执行 Group 是两个生命周期阶段。

## 8. 注册故事

**内核 registry-agnostic。** 节点类型经宿主传入:

```python
types = {**PRIMITIVES, "MyNode": my_node_type}   # 宿主决定全集
result = GraphInstance.build(definition, types, asset_resolver=host_resolver)
```

- `NodeType` 是公开的 frozen dataclass,外部包直接构造即完成"注册"——没有
  内核内注册表、没有装饰器、没有 import 钩子。
- `PRIMITIVES`(`eidolon_primitives` 包)只是内置验证原语的便利集合:
  **内置节点 = 包概念,不是内核概念**。
- 内核不认识"实现来源"概念:code / script / LLM / 远程——全部归结为一个
  `NodeType` 值。

## 9. ABI 稳定性承诺

外部实现者**可以依赖**(锁定):

- 端口 / 组 / Readiness 的声明语义与派生判定(§2);
- `handler(ctx: GroupContext) -> GroupOutput` 调用形状与 `GroupContext`
  字段白名单 `{group, data_in, state, config, assets}`、`InitContext` 字段
  白名单 `{config, assets}`;
- 端口分区(输入端口唯一归属一个 Group)、Signal 双语义(§2.5);
- Readiness 计算(§2.4)、错误约定(§3.3)、值域与零拷贝约定(§5);
- 不写即不投递;写必须属于本组;
- 执行时长不属于传播语义、内核无等待状态(§6);
- tags 不参与执行(§2.7)。

内核**不承诺**:

- 共享资产的调用顺序(属资产系统与编排节点的责任);
- 节点执行时长的任何排序含义(§6);
- 反射绕过信任模型外的行为(与"节点不得原地修改共享载荷"同一信任模型)。

## 10. 边界裁定表

| 边界 | 裁定 |
|---|---|
| 执行单位 | **Group**(调用契约);Node 是容器,无执行语义 |
| 输出授权 | `Group.outputs`;handler 只能写本组声明;节点级 data_out 降为端口清单 |
| 源 | 废除;宿主注入唯一入口;Source 类节点 = tags("source") + 宿主注入驱动 |
| 资产所有权 | 使用权非所有权;节点级共享,不按组切分 |
| 值域 | State/Data/Event 载荷 = Value;Capability 不得进入传播/状态平面 |
| 内核内部访问 | 节点可见面 = GroupContext 五字段 + InitContext 两字段 |
| 调用顺序 | 共享资产调用顺序不构成 Runtime 语义 |
| 执行时长 | 不属于图传播语义;不构成事件排序依据 |
| Signal | 双语义:绑定控制(源选择)或纯数据输入;不参与 Readiness、无节点级门控 |
| 标签 | tags 描述层;执行路径禁止读取;不得成为隐式行为开关 |
| 注册 | 内核 registry-agnostic;宿主传 types |
| 初始化 | 仅构建期 init 一次;无运行时生命周期钩子 |

## 11. 验证边界

原 14 例边界验证(2026-08-21)随本修订部分翻转,**重验清单随实现更新**。
保留不动的核心验证:错误约定、资产四概念、使用权非所有权、init 系列
(载体改宿主注入)、Activation/Event 契约(§6)。新增验证:

1. 多组合 handler:同一节点多组独立执行、独立 NodeTurn 预算;
2. 端口分区:输入端口唯一归属组;组间状态经节点状态传递;
3. Readiness DSL:四种旧策略的谓词实例 + 空集语义 + 默认空组报错;
4. Signal 绑定:模式切换(LOW/HIGH 翻转重估)、1:1 绑定校验、未激活默认
   数据有效、受控输入必须连线;
5. 纯数据输入:未绑定 SignalIn 按数据聚合,handler 读 level;
6. config 三节嵌套:合并链、白名单、init_effective 与 group_effective 隔离;
7. tags 不参与执行;输出授权 vs group.outputs;handler 共享不产生分发。
