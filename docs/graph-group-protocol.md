# 节点组协议(Group-centric 内核修订裁定)

> 状态:已裁定(2026-08-22),实现与测试翻转已完成(2026-08-23 语义测试 89/89 全绿)
>
> 文档角色:**本文件记录修正方案与裁定过程;最终规范文档为
> [graph-node-protocol.md](./graph-node-protocol.md)**(§14-4 决议:需修正部分就地重写)。
>
> 定位:本裁定修订 `graph-node-protocol.md` 的节点定义层与运行层——内核执行模型
> 从 Node-centric 迁移为 **Group-centric**。修订动机来自对冻结内核的同步审查
> (2026-08-22):当前实现把本应属于"输入组协议"的语义压缩到了节点级
> `tick + ctx.group` 字符串分发中,组被降格为运行时标签,输入组↔输出组的
> 调用契约关系缺失,`is_source` 播种与节点级 `enable` 属架构污染,Signal
> 参与 Readiness 属职责泄漏。
>
> 相关:被修订的冻结协议 [graph-node-protocol.md](./graph-node-protocol.md);
> 端口行为参照 [eidolon-graph/docs/graph-ports-bindings.md](../eidolon-graph/docs/graph-ports-bindings.md);
> 资产层不受本修订影响 [graph-assets.md](./graph-assets.md)。

## 1. 背景:同步审查发现的偏离

审查自 `model/node_type.py` 开始,逐文件比对设计预期。发现五处偏离:

1. **输入组/输出组绑定缺失**:设计预期为"输入组 = 对外接受数据的接口、
   输出组 = 返回值集合、二者显式绑定";实现只有节点级 `data_out` 清单,
   输出声明不归属任何组。
2. **tick 未按组对应**:设计预期"tick 的数量与输入组对应";实现为单一
   `NodeType.tick` + `ctx.group` 字符串分发(Buffer 的 `if ctx.group ==
   "put"` 是铁证)——组不是行为的绑定边界,只是统一函数的字符串参数。
3. **is_source 架构污染**:内核从"无输入组"这一形状推导"源节点"并按
   epoch 播种执行——事件系统应当只认识事件传递,不应当认识"谁产生事件"。
4. **信号职责错位**:设计预期"输入信号控制对应绑定的数据输入端口";
   实现演化为节点级 enable(门控整节点)+ 资格槽(参与 Readiness)两种
   执行调度机制——Signal 泄漏进了执行调度层。
5. **to_dict 观察缺口**:有 `has_init` 无 `has_tick`,输出侧只暴露端口名。

**核心诊断:Node-centric vs Interface-centric**。执行器内部已出现裂缝——
readiness、消费、NodeTurn 预算都已是组粒度,唯独执行入口 `ntype.tick(ctx)`
与输出授权仍锚定节点。**Node 本身没有执行语义;Group 才是执行单位**。

方法论注记:117 个冻结测试证明的是当前 Semantic Baseline 的**内部一致性**;
本修订是第二阶段的**领域模型一致性审查**——测试服从裁定,而非裁定服从测试。

## 2. 领域模型总纲

```text
Node = 容器(Identity / Ports / Assets / State / Config / Groups / Tags),无执行语义
Group = 调用契约(一次可触发行为):inputs + triggers + readiness + handler + outputs
Port = 连接 ABI(节点级声明,Wire 身份 (node, port, slot) 不变)
```

执行链:

```text
Injection → Event → Wire → Port → [端口模式判定] → Group Readiness
         → Handler → GroupOutput(⊆ Group.outputs) → Event → ...
```

两层正交架构:

```text
事件传播层(Port-centric,冻结基线零改动)
    Event → Wire → Port → Port 事实(value/level/pending)
                                │
                                │ 静态归属(声明层分区,非运行时投影)
                                ▼
行为层(Group-centric)
    Port 所属 Group → Readiness → Handler → GroupOutput
```

**端口分区**:每个输入端口在声明层**唯一归属一个 Group**。因此 pending
属于 Port(端口级事实)天然就是组局部的——无需任何运行时投影;事件系统
完全不感知 Group 的存在。

## 3. 裁定记录

| # | 裁定 | 内容 |
|---|---|---|
| 1 | 组 = 函数调用契约 | 每组独立 handler;输入组↔输出组 1:1;触发 = 给默认策略加门的声明;组触发机制与组行为分离(默认处理机制可被重载) |
| 2 | is_source 废除 | 内核只认识事件传递,不推导"源";Source 降为 role tag(描述层) |
| 3 | Port / Group 正交 | Port = 连接 ABI;Group = 行为 ABI;handler 只能写本组 `outputs`;节点级 `data_out` 降为纯端口清单 |
| 4 | Event 身份 Node-level | Group 进时间线 fire 条目(执行记录维度),不进 Event identity |
| 5 | 无节点级 enable | 执行资格全部归属 Group Readiness(后经 #10 进一步修订) |
| 6 | 宿主注入唯一入口 | 无播种;`run([])` = 立即静止;标签不得成为隐式行为开关 |
| 7 | 命名 | `Group` / `GroupContext` / `GroupOutput`;`tick` 退役 |
| 8 | OutputGroup 不实体化 | `Group.outputs` 即输出授权集合(1:1 绑定下输出组身份 = 组身份) |
| 9 | 默认空 Group = 构建错误 | 防止"永远 ready"的自动执行契约借壳复活 Source 语义 |
| 10 | handler 可共享 | Group 身份来自 Group 本身而非 callable 身份;禁止依赖 ctx.group 分发 |
| 11 | 空集语义 | `ALL()=True`、`ANY()=False`;默认推导的 Trigger 门是**条件增补**(triggers 非空才加门) |
| 12 | 资源三层 | Asset = 节点成员环境;Config = 实例级行为参数覆盖;Default = 定义层默认参数;Config 按 Group 分层嵌套;`config_defaults` 拆为 `init_defaults` |
| 13 | 端口分区 | 输入端口唯一归属 Group;事件系统 Port-centric 零改动(撤回 (Group,Port) 投影与 consumed_by 组维度) |
| 14 | Signal 归位 | Signal 退出 Readiness;= DataIn 的数据来源选择/激活模式控制(见 §6);SIGNAL/SIGNAL_HIGH 叶条件撤销 |
| 15 | Signal 绑定细则 | 未激活信号 = 默认数据有效;SignalIn↔DataIn 严格一对一;未绑定 SignalIn 可单独存在;LOW = 不等待 + 回默认数据 |
| 16 | 无触发器组要求新事实 | 缺省 readiness 下,无触发器组须至少一个输入 pending 才触发;全静态回退值不再在任意节点唤醒时连带触发(裁定 9 精神的延伸;DSL v2 迁移实测暴露,2026-08-22) |
| 17 | 空组一律构建错误(2026-09-05) | 无 inputs 且无 triggers 的组**即使携带显式 readiness** 也构成构建错误(语义闭包审计 R6 裁定)——零端口组的谓词只能是常量:恒真 = 每次节点唤醒触发且不消费任何事实,恰是裁定 9/16 禁止的"永远 ready"自动执行契约借壳。内核裁定 → NodeType 不变式 → DSL 自动继承(无 DSL 层专属规则) |

## 4. 声明层

### 4.1 NodeType

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
    # ---- 行为 ABI ----
    groups: tuple[Group, ...] = ()
    # ---- 描述层(执行路径禁止读取)----
    tags: tuple[str, ...] = ()
    # ---- 构建期钩子(不变,graph-node-protocol.md §7)----
    init: Any = None    # init(ctx: InitContext) -> dict | None

    # 删除:tick / config_defaults / is_source
    # 保留:is_signal_node(纯派生观察)/ port / out_port / group 查询
    # to_dict 修复:tags、每组 handler 存在性/outputs/defaults/readiness
```

### 4.2 Group(调用契约 = 执行单位)

```python
@dataclass(frozen=True)
class Group:
    name: str
    inputs: tuple[str, ...] = ()        # 读哪些 DataIn / 未绑定 SignalIn(本组私有,
                                        # 分区;信号输入按数据处理,§14-1)
    triggers: tuple[str, ...] = ()      # 非空 = 给默认策略加 Trigger 门
    outputs: tuple[str, ...] = ()       # 输出授权集合(⊆ data_out ∪ signal_out 名)
    defaults: dict[str, Any] = field(default_factory=dict)  # 行为参数默认值(定义层)
    handler: Any = None                 # 必填;可跨组共享,禁止依赖 ctx.group 分发
    readiness: Readiness | None = None  # 显式谓词;None → 默认推导
```

### 4.3 端口

```python
@dataclass(frozen=True)
class DataIn:
    name: str
    default: Any = None
    cache: str = REPLACE
    signal: str | None = None   # 绑定的 SignalIn 端口名;None = 无绑定(§6)

# DataOut / TriggerIn / SignalIn / SignalOut / AssetIn:形态不变
# 删除:DataIn.qualified 与 SLOT_QUAL(资格槽机制整体撤销)
```

### 4.4 分区与声明校验(构建期)

1. `group.inputs` ⊆ data_in ∪ signal_in 名(未绑定 SignalIn 按数据输入);
   `group.triggers` ⊆ trigger_in 名;`group.outputs` ⊆ data_out ∪ signal_out 名
2. `group.handler` 非 None
3. 显式 `readiness` 引用端口 ⊆ `inputs ∪ triggers`
4. **每个输入端口恰属于一个 Group**(重复归属/未归属 = 构建错误;
   未归属端口的严格读法见 §14-2)
5. 空 Group(inputs/triggers 皆空)= 构建错误,显式 readiness 不豁免(裁定 17)
6. `DataIn.signal` 引用已声明 SignalIn;严格一对一(每个 SignalIn 至多被
   一个 DataIn 绑定);未绑定 SignalIn 合法(纯信号节点形态)
7. config 三节白名单(§7)

## 5. Readiness

```python
# model/readiness.py
ALL(*conds) / ANY(*conds)   # 可嵌套;空集:ALL()=True, ANY()=False
DATA(port)      # 动态端口:pending;静态端口:真空为真(§6 模式规则);
                # 对未绑定 SignalIn 输入同样适用(pending,数据化处理)
TRIGGER(port)   # pending(Data Event = 载荷 + 激活;Signal Event = 纯激活)
```

默认推导:

```python
default_readiness(g) =
    ALL(DATA(p) for p in g.inputs)
    ∧ (ANY(TRIGGER(t) for t in g.triggers)   若 g.triggers 非空)
```

- 默认 = 数据齐集自动处理;声明 triggers = 给默认策略加门
  (Latch 形态成为声明 triggers 的自然默认)
- 旧四策略全部是 DSL 实例:ON_ALL_DATA_READY = 默认;ON_ANY_DATA =
  `ANY(DATA(...))`;ON_TRIGGER = `TRIGGER(...)`;ON_DATA_AND_TRIGGER =
  默认(含 triggers)或显式 `ALL(DATA(...), TRIGGER(...))`
- **Signal 不进入谓词**(裁定 #14)

## 6. Signal:DataIn 动态绑定控制

Signal 的职责严格停留在**输入端口的数据来源选择/激活模式控制**,不参与
"Group 什么时候执行"的判断。绑定关系(`DataIn.signal`)本身才是关键——
这是对 graph-ports-bindings.md §4 的修订:资格门控撤销,源选择取代。

信号具备两种语义(§14-1 决议):绑定数据端口 = 控制(本章);未绑定数据
端口 = 纯数据输入,组按数据处理(§4.2 inputs 可引用未绑定 SignalIn)。

### 6.1 模式规则(静态/动态吸收的第三决定因子)

```text
signal_active(p) =
    无绑定 或 绑定信号未连接   → True(无控制器,行为与旧模型相同)
    已绑定且已连接             → level == HIGH
    (level None = 未激活 = False,裁定 a:未激活默认数据有效)

动态模式 ⇔ (连接数据线 或 曾注入) ∧ signal_active(p)
静态模式 ⇔ 其余一切情况
```

| 信号状态 | 数据端口模式 | DATA 叶求值 | 执行时 effective argument |
|---|---|---|---|
| 未激活(LOW / None / 未连接) | 静态 | 真空为真(**不等待**,裁定 d) | `config.ports[p]` → `DataIn.default` |
| 激活(HIGH) | 动态 | 需 pending(**必须等待**) | 缓存值(无动态数据回落默认) |

### 6.2 运行语义

- 数据事件照常进入、照常缓存(与信号状态无关);fire 时统一消费本组端口
  pending,value/level 保持
- 信号事件:level + pending 更新 → 唤醒节点 → 访问时消费 pending(仅触发
  重估);电平翻转即刻改变后续解析,无任何门控语义
- **推论**:旧"静态端口 + 资格槽"(受控默认参数)模式死亡——"HIGH = 必须
  等待"要求动态源存在,受控输入的静态形态必须改为连线
- LOW 自消费机制全家(资格槽配对、deliver-path 自消费、静态资格特殊逻辑)
  整体删除

## 7. 配置与资源三层

### 7.1 三个语义层

| 层 | 定位 | 落点 |
|---|---|---|
| Asset | Node 成员环境(节点级、所有组共享、不在覆盖链上) | `ctx.assets` |
| Config | 实例级行为参数覆盖(加载期可改、运行期只读) | `NodeSpec.config`(嵌套) |
| Default | 定义层默认参数 | `Group.defaults` / `DataIn.default` / `init_defaults` |

### 7.2 实例 Config 三节

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
  handler 如何取值是实现细节

### 7.3 协议类型

```python
@dataclass
class GroupContext:
    group: str                # 信息性(裁定 #10:禁止依赖其分发)
    data_in: dict[str, Any]   # 组内 inputs effective 值 + Trigger 载荷
    state: dict[str, Any]     # 深拷贝
    config: dict[str, Any]    # 本组 effective config(§7.2)
    assets: dict[str, Any]    # 浅拷贝(节点级共享,不按组切分)

@dataclass
class GroupOutput:
    data_out: dict[str, Any] = ...    # 键 ⊆ group.outputs;违规 = KIND_ERROR + 丢弃
    signal_out: dict[str, bool] = ... # 同上
    state: dict[str, Any] = ...       # 增量;未知字段/不可复制 = KIND_ERROR(不变)

@dataclass
class InitContext:
    config: dict[str, Any]    # init_effective
    assets: dict[str, Any] = ...
```

## 8. 执行层

### 8.1 epoch 语义

```text
run(injections):注入按序入队 → worklist 脏传播(投递唤醒、深度优先)
               → 组声明序求值谓词 → ready 即 fire
               → 每组每 epoch 至多一次(NodeTurn 预算,(节点, 组))
               → 队列排空即静止
```

无播种、无源扫描;`run([])` = 立即静止。宿主节奏经 Injection 表达
(Source 类节点由宿主注入系统事件驱动,内核不区分事件来源)。

### 8.2 fire 流程

```text
谓词满足
  → 逐端口模式判定 + effective 解析(§6)
  → GroupContext 构造(state 深拷贝 / config 组合并 / assets 浅拷贝)
  → group.handler(ctx)
  → 消费本组端口 pending
  → 状态提交(增量,未知字段/不可复制 = KIND_ERROR)
  → 输出校验(键 ⊆ group.outputs,违者 KIND_ERROR + 丢弃)
  → 产出即时投递(零拷贝探针)
```

### 8.3 保留(冻结语义,不动)

NodeTurn 预算、异常约定(tick 异常 = 无输出 + pending 保留 + 下 epoch 重试)、
值域三入口探针、零拷贝共享约定、投递深度优先、反馈环跨轮迭代、
静态/动态吸收(扩展后)、init 构建期钩子、资产四概念。

### 8.4 删除

`SOURCE_GROUP`、播种段、`enable_states`、`qual_states`、`_static_qual_ok`、
deliver-path 自消费、`Policy` 枚举、`ctx.group` 分发。

## 9. 事件层(零改动)

Event / Delivery / consumed_by / status / pending 机制**逐字节保持冻结基线**
(裁定 #4、#13):

```text
Event identity = id / run / kind / payload / producer(节点级) / port
Delivery → 唯一目标 Port → 唯一所属 Group(经声明层静态推导,非运行时字段)
Event 不携带任何 Group 字段;投递路径不遍历组
```

一事件激活多组经扇出实现(多 Wire → 多 Delivery → 多 Port → 各属其组)。

## 10. 与冻结基线差异清单

| 维度 | 冻结基线 | 本修订 | 依据 |
|---|---|---|---|
| 执行单位 | Node(单一 tick) | Group(每组合 handler) | #1 |
| 输出授权 | 节点级 data_out 清单 | `group.outputs` | #3 |
| 组身份 | `ctx.group` 字符串 | 行为绑定边界 | #1 |
| 源 | `is_source` 形状推导 + 播种 | 删除;宿主注入 | #2,#6 |
| Source 分类 | 结构推导 | `tags`(描述层) | #2 |
| enable | 节点级门控 | 删除 | #5 |
| 资格槽 | DataIn.qualified + qual 槽位 | 删除 | #14 |
| Signal | 参与 Readiness / 门控 | DataIn 源选择(§6) | #14,#15 |
| 策略 | Policy 封闭枚举 | Readiness 谓词 DSL | #11 |
| config | 节点级扁平 | 三节嵌套 + init_defaults | #12 |
| pending | 端口级 | 端口级(分区保证组局部) | #13 |
| Event 模型 | — | 零改动 | #4,#13 |

## 11. 原语修订(eidolon_primitives)

统一 `_ENABLE` 惯例删除;SignalIn 只在有绑定的节点声明:

DSL v2 迁移后,Primitive 的 ABI 名以实际编译产物为准(2026-08-23 修订)。
命名链:handler 函数名 = Group identity(裁定 1);组内端口 = `{group}.{param}`
(裁定 2);`-> T` 输出端口 = 组名;`@group(outputs=(...))` 扩展输出端口为
`{group}.{name}`;触发器端口默认 `{group}.trigger`,`@group(trigger=...)` 可
解耦端口名末段(撞 Python 关键字,如 `pass`)。

| Primitive | DSL handler(@group 函数) | 编译后 Group | 编译后 Ports(组限定) |
|---|---|---|---|
| Source | `tick` | `tick` | `tick.trigger`(TriggerIn);`tick`(DataOut) |
| Constant | `tick` | `tick` | `tick.trigger`(TriggerIn);`tick`(DataOut) |
| Sink | `consume` | `consume` | `consume.value`(DataIn) |
| Probe | `observe` | `observe` | `observe.value`(DataIn) |
| Buffer | `put` / `flush` | `put` / `flush` | `put.item`(DataIn,APPEND);`flush.trigger`(TriggerIn);`flush`(DataOut) |
| Join | `join` | `join` | `join.a`、`join.b`(DataIn);`join`(DataOut) |
| Split | `fan` | `fan` | `fan.value`(DataIn);`fan.out1`、`fan.out2`(DataOut,`outputs=` 扩展) |
| Latch | `release` | `release` | `release.gate`(SignalIn)、`release.trigger`(TriggerIn)、`release.data`(DataIn,`Gated[int,"gate"]`);`release`(DataOut) |
| DataToSignal | `convert` | `convert` | `convert.data`(DataIn);`convert`(SignalOut) |
| SignalToData | `pass_value` | `pass_value` | `pass_value.gate`(SignalIn)、`pass_value.pass`(TriggerIn,`trigger="pass"` 解耦)、`pass_value.x`(DataIn,`Gated[int,"gate"]`,**必须连线**);`pass_value`(DataOut) |

- `Split.fan` 是 Group;`out1` / `out2` 是该组的输出端口,不是独立组名。
- `SignalToData`:handler 名 `pass_value`(`pass` 是 Python 关键字,不能作
  函数名);Group identity 仍为 `pass_value`;`trigger="pass"` 仅解耦触发器
  端口名末段 → `pass_value.pass`。
- 宿主驱动:`world.run([Injection("src", "tick.trigger", SLOT_TRIGGER, Kind.SIGNAL, True)])`
- SignalToData 新语义 = 受控源选择:gate LOW → pass 触发即输出 config
  默认;gate HIGH → 必须等 x 数据到达、输出 x
- Latch/SignalToData 的新形态确认见 §14-3

## 12. 测试影响

| 类别 | 内容 |
|---|---|
| 翻转(B) | 配对 5 例 + LOW 自消费一致性(资格槽语义撤销)、enable 门控 2 例(节点级语义撤销) |
| 改声明(A) | 政策 4 例、预算、扇出、反馈环、异常约定、init 七连测、资产四概念、Activation/Event |
| 删除载体(D) | 播种 2 例(注入序 vs 播种、每 epoch 播种);init 播种载体改宿主注入 |
| 新增 | 信号模式切换(LOW/HIGH 翻转重估)、1:1 绑定校验、未绑定 SignalIn 合法、空组报错、tags 不参与执行、嵌套 config 三节、handler 共享、输出授权 vs group.outputs |

## 13. 实施范围

```text
第 1 步 model 层:ports.py → readiness.py(新) → node_type.py → graph.py → validate.py → __init__.py
第 2 步 engine 协议层:protocol.py → port_state.py(微调) → event.py(零改动确认)
第 3 步 engine 执行层:executor.py → instance.py → console.py → __init__.py
第 4 步 eidolon_primitives 重写 + examples 重写
第 5 步 tests 翻新(§12)
第 6 步 文档修订:graph-node-protocol.md §1-§3/§5/§8/§10-11 指向本文档;README 更新
```

## 14. 决议记录(2026-08-22)

1. **信号双语义**(决议):信号具备两种语义——与数据端口绑定时**控制数据
   端口**(§6 源选择);不绑定数据端口时作为**纯数据输入**——信号与数据
   同样处理,都是事件携带的数据。组按数据处理信号输入:readiness 按
   pending 聚合(与数据输入一致),handler 经 `ctx.data_in[端口名]` 读 level。
2. **不区分观察/吸收端口**(决议):两者在事件角度上,一个是"接收输入事件
   的同时立即输出类似事件"(观察),一个是"接收但不产出"(吸收)——都是
   普通节点形态(普通组 + 普通 handler),不需要端口类别;§4.4-4 的严格
   读法成立(每个输入端口恰属于一个 Group)。
3. **Latch/SignalToData 新形态**:暂缓讨论。
4. **文档分工**(决议):`graph-node-protocol.md` 对需要修正的部分**直接
   重写**(最终规范文档);本文档保留为修正方案记录。
