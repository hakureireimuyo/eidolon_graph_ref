# 节点协议(内核 ↔ 外部节点 ABI)

> 状态:已冻结(2026-08-22 裁定修订,参考实现已迁移;2026-08-23 语义测试 89/89 全绿锁定,旧基线测试已翻转完成)
>
> 定位:本文档是节点协议的**最终规范文档**——内核与外部/自定义节点实现之间的
> 唯一契约(ABI)。Group-centric 修订的修正方案与裁定过程记录于
> [graph-group-protocol.md](./graph-group-protocol.md)。
>
> 本次修订:**§1 增补总体架构与所有权边界(核心原则)、§2 节点定义层、
> §3 运行层、§5 内核边界层、§9-§11 重写**;
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

### 1.0 总体架构与所有权边界（核心原则）

**核心原则(一句话):**

> **NodeDefinition inheritance is declarative, not behavioral.**
> 节点定义的继承表达"这是一个合法的节点定义"(声明资格),而不是"从父节点
> 继承运行时行为"。所有节点共有的默认运行语义由 Kernel 统一提供,而非由
> NodeDefinition 基类提供。"具体节点禁止二次继承"只是这条原则的一条推论,
> 不是原因。

**三层架构:表达语言 → 语义 IR → 执行器。**

```text
            Node Definition DSL(受约束的表达语言)
                          │ 函数签名即组协议(@group / this / 注解词汇)
                          ▼
                    NodeDefinition(声明资格:编译入口)
                          │
                          │ compile(类创建期;AST / 类型解析 / 声明校验属编译过程)
                          ▼
                       NodeType(语义 IR / Node ABI:已解析的节点契约)
                          │
                          │ 被 Kernel 解释(IR 是内核的唯一输入形态)
                          ▼
                    NodeSemantics(Kernel final:readiness / consume /
                          │        解释矩阵 / 静态动态吸收 / 组执行)
                          ▼
                      Executor(时间线与 worklist 编排)
```

`class MyNode(NodeDefinition)` 不是传统 OOP 实现继承——不存在"继承
fire() / receive()"这种事——而是**声明资格**:编译器把声明编译成
`MyNode.TYPE`,Kernel 再对 TYPE 统一施加运行语义。运行时不存在节点对象。

**NodeType 的独立身份:语义 IR。**

- NodeType 是 DSL 的编译目标,不是"缓存后的 Python class"——它是脱离
  Python 后依然成立的契约描述:冻结 dataclass,可序列化、可 to_dict,
  编辑器平面与运行时平面共享同一份 IR。
- 运行时执行的是语义,不是语法:内核不重新理解函数签名,只查询已解析的
  inputs / triggers / outputs / readiness / defaults / handler。
- 判断标准:任何存在于 DSL 与 NodeType 之间的东西,必须回答"它是否具有
  独立的语义职责"。字符串中转之类的临时表示应当消失;AST、类型解析、
  声明校验可以作为编译过程存在,但它们不是 ABI。

**所有权表:**

| 东西 | 所有者 | 作用 |
|---|---|---|
| 节点声明协议 | `NodeDefinition` | 规定什么样的 Python 定义可被编译成 `NodeType` |
| 语义 IR(Node ABI) | `NodeType` | 已解析的节点契约;DSL 的编译目标、内核的唯一输入、编辑器的序列化边界 |
| 普遍执行语义 | Kernel / `NodeSemantics` | 规定任何合法 `NodeType` 的运行规则,不可重载 |
| 具体领域行为 | Concrete Node | handler 如何根据自己的状态计算输出 |
| 共享领域行为 | Definition Material | 普通类(不编译 TYPE),被多个具体节点复用(§2.0) |
| 事件传递 | Kernel | 在地址之间传递事件 |

**`NodeDefinition` 与 `NodeSemantics` 的严格区分。**

- `NodeDefinition` 回答:"我有哪些端口?哪些输入组?哪些资产声明?handler
  是谁?状态 schema 是什么?"——它**不得**提供 receive / consume / fire /
  emit 等运行行为,越薄越好。
- `NodeSemantics` 规定:"什么叫 ready?何时消费?Data / Signal / Trigger
  如何解释?静态 / 动态如何吸收?何时 fire?GroupOutput 如何变成 Event?"
  ——Kernel final,`__init_subclass__` 拒绝子类化(§3.0)。

**内核的稳定边界。**

内核不理解节点的领域身份——不存在 `if node_type == Buffer` 分支——只理解:
事件、地址(节点 id + 端口 + 槽位)、端口状态、组协议、统一 handler ABI。
运行事实链:

```text
Event → (address, payload) → port state → group readiness
      → handler(ctx) → GroupOutput → Event
```

内核的输入 = Event / NodeType / State,输出 = Event / State。Buffer、LLM
与未来完全未知的节点,对内核都只是满足同一协议的 `NodeType`。

**输入组 = 节点行为的真正边界。**

节点的全部创造性集中在 ports / groups / predicates / handler / state;
其余是内核统一语义。共享领域行为属于独立 Definition Material,不属于任何
具体节点——这也是 `BufferMaterial` 方向合理的依据:材料提供节点定义材料,
但不能变成第二套 Kernel 语义。

**为新子系统建立的设计清单(本子系统的经验沉淀,2026-08-22)。**

本子系统从"内核语义 → NodeType 随实现沉淀 → 事后发现表达层"的路径,
沉淀为一条方法论:设计任何新的声明体系(Asset Definition / Character
Definition 等)时,从第一天就把**语义模型**与**表达语言**并列为两个
独立设计维度:

1. 它的运行语义是什么?
2. 它的稳定 IR 是什么?
3. 它的合法表达语言是什么?
4. 哪些非法状态应该由语言直接消除?
5. 哪些问题必须留给运行时处理?

## 2. Node 定义层

### 2.0 Node Definition Language（定义期，不进入 Kernel）

`NodeDefinition` 是**具体节点的声明入口**，不是领域行为的公共基类；它不是
图中的节点实例。类创建时，`NodeDefinitionMeta` 绑定 handler、检查声明，并
把类体编译成独立的 `NodeType`：

```text
class Buffer(NodeDefinition) → 类创建期校验/编译 → Buffer.TYPE → GraphInstance
```

Kernel 只接收 `TYPE`，不保存 Python class、MRO、父类或虚函数表，也绝不构造
`NodeDefinition` 实例。定义语言使用 `GroupSpec(handler="method_name")` 引用
`@staticmethod` handler；编译后才成为 `Group(handler=callable)`。

**能力所有权边界（冻结）**：

```text
普遍节点语义（事件解释矩阵 / 执行生命周期 / 构建管线）→ Kernel（final，自动生效）
节点特有的共享行为 → 独立定义材料（普通 Python 类，不编译 TYPE、不进 Kernel）
Concrete NodeDefinition ──❌──► Concrete NodeDefinition
```

具体节点之间**不形成行为继承关系**：一个具体节点不能成为另一个具体节点的
行为供应商。`Buffer → RingBuffer` 是错误的建模方式——RingBuffer 需要 Buffer
的共享部分时，应把可复用部分提取为独立材料（如 `BufferMaterial`），由两个
平行节点共同引用：

```text
Buffer     = NodeDefinition + BufferMaterial
RingBuffer = NodeDefinition + BufferMaterial（同名 staticmethod 遮蔽单个行为）
```

材料复用（mixin）是当前非正式实现通道，最终形态暂不冻结：材料作者自行
负责字段 / handler 不冲突（MRO 同名字段静默后胜，无强制冲突检测）。第一个
真实共享案例出现后，再裁定是否需要正式的组件形态。

定义期错误（非静态 handler、非单一必填 `ctx` 参数、继承具体节点定义等）
抛出 `DefinitionError`，早于图校验与 `GraphInstance.build()`。

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

端口事件的解释属于内核 final 基类 `engine/node_semantics.py::NodeSemantics`，
而不属于 `NodeDefinition` 或 Group handler：Data Event 只能更新 DataIn cache，
Signal Event 只能更新 SignalIn level，二者都可投递 TriggerIn（分别携带载荷/
纯激活）。该基类再依据 `DataIn.signal` 解释静态/动态数据来源与 DATA leaf。
它不可继承或覆盖；Executor 仅负责时间线与 worklist 编排。

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

### 3.0 事件解释矩阵（内核 final：NodeSemantics）

Data / Signal / Trigger 三种事实与槽位的正交组合如何被解释——§2.5 的静态/
动态吸收、绑定信号的控制消费、触发载荷并入实参、组消费——是内核 final
协议行为，集中在 `engine/node_semantics.py` 的 `NodeSemantics`。定义层不
存在可重载覆盖点（§2.0 能力所有权边界：具体节点之间不形成行为继承关系），
`__init_subclass__` 直接拒绝子类化。

```text
receive            投递槽位写入：data/signal/trigger 三种状态的唯一入口
consume            端口状态 pending → consumed（时间线消费记录）
settle_control     绑定 SignalIn 的唤醒-消费：控制态更新，level 保持
dynamic/effective  DataIn 与绑定 SignalIn 的正交解释：静态/动态吸收（§2.5）
handler_arguments  组实参解析：effective 逐端口 + 触发载荷
group_ready        谓词求值（DATA/TRIGGER 叶，§2.4）
consume_group      组消费：输入（Data 或未绑定 Signal）+ 触发器载荷清除
```

执行器（`engine/executor.py`）只做编排：注入/产出事件的建档、worklist 脏传播、
NodeTurn 预算、handler 调用、输出校验与扇出投递——不含任何解释逻辑。
运行态不存在节点对象：矩阵解释直接作用于 `GraphInstance` 的端口状态。

### 3.1 构建管线

```text
结构校验(validate:类型存在 / config 三节白名单 / 连线 kind / 扇入 / 绑定结构)
    → config 值域探针(三节递归,Value:可复制)
    → 声明校验(§2.2-§2.5:端口归属分区、handler 非空、readiness 引用、
      空组、signal 绑定 1:1)——内联于 NodeType 构造(构造即校验,
      任何构造路径不可绕过;DSL 编译期转 DefinitionError)
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
  → 逐端口模式判定 + effective 解析(§2.5,内核 final §3.0)
  → GroupContext 构造(state 深拷贝 / config = group_effective / assets 浅拷贝)
  → group.handler(ctx) -> GroupOutput | None
  → 消费本组端口 pending(§3.0;value/level 保持)
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
注入)、零拷贝共享约定(扇出共享载荷引用;Data payload 进入 Data Plane
即视为不可变值,节点禁止原地修改输入)、投递深度优先、反馈环跨轮迭代、
静态/动态吸收。

Ownership 边界(裁定 2026-08-23):State 事务 = 工作副本(每轮 fire
deepcopy)+ 全量提交(整值赋值与原地变异均生效,失败丢弃);State→Data
输出不得以隐式 alias 泄漏 state 持有对象——同一引用时输出侧复制;但
**Move 合法**:节点显式把对象移出 State(`this.items = []; return items`)
时,ownership 转移给 Data Plane,零拷贝释放(`Buffer.flush` 示范)。
Data→Data 传播零拷贝。冻结的是「谁拥有对象、谁能改对象」——隐式
alias 解除需复制,显式 ownership transfer 不复制;复制只发生在解除
ownership alias 的边界,而非传播路径。

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
