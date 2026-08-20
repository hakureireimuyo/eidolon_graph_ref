# 资产协议与资产管理系统协议(节点 ↔ 资产 ↔ 资产管理系统)

> 状态:已裁定 + 已验证(2026-08-20,§27 边界全部通过)
>
> 定位:本文定义 Eidolon 中 Node、Graph Runtime、Asset 与 Asset Management
> System 之间的协议边界,以及资产能力的注入、使用、实例化、共享和生命周期
> 管理方式。本文建立在 [graph-assets.md](./graph-assets.md) 已完成的
> Asset 层验证基础上:`graph-assets.md` 解决的是"资产属于谁、如何绑定、
> 何时解析以及如何保证节点获得有效资产"的问题;本文进一步定义"节点如何
> 使用资产、资产如何实现能力、资产管理系统如何管理资产"。

## 1. 核心裁定

Eidolon 将运行系统划分为四个具有明确职责的层次:

```text
Graph Runtime
    │
    │ 定义运行时机、事件传播、节点调度
    ▼
Node
    │
    │ 声明并调用 Capability Protocol
    ▼
Asset
    │
    │ 提供具体能力实现
    ▼
External System / Resource
```

与此同时,Asset Management System 位于 Asset 的外部,负责资产实例的创建与管理:

```text
                Asset Management System
                 │       │       │
          初始化参数   实例化   生命周期
                 │       │       │
                 ▼       ▼       ▼
              Asset Instance
                 │
                 │ Capability Protocol
                 ▼
                Node
```

其中最重要的边界是:

> **Node 定义如何编排能力,Asset 定义能力本身如何实现,Asset Management System 定义能力实例如何被创建和管理。**

节点不拥有资产的创建权、销毁权或生命周期管理权。

资产不负责决定图中的事件传播顺序,也不负责决定哪个节点什么时候执行。

资产管理系统不参与 Graph Runtime 的事件传播与节点调度。

### 1.1 一个资产实例,两个协议表面

资产实例同时面对两个调用方,但两个调用方的契约**完全分离**:

```text
                      Asset 实例
        ┌─────────────────┼─────────────────┐
        ▼                                   ▼
    使用面(Capability)                 管理面(Asset 管理协议)
    节点唯一可见                       只有资产系统可见
    "如何使用"                         "如何初始化 / 监控 / 销毁"
    query() / generate() / embed()     init(params) / health() / close()
```

- **使用面** = `AssetIn.type` 声明的能力接口(runtime_checkable Protocol);
- **管理面** = 资产系统按种类注册的工厂与生命周期接口;
- 同一实例两套表面,而非两个对象:资产系统把"使用面投影"注入节点,
  管理操作全部留在资产系统内部。节点即使拿到对象,契约上也不含任何
  管理方法(graph-assets.md §2-5:构建期类型检查即不变量执行点);
- 分离的意义:节点永远不知道资产怎么初始化、怎么恢复、怎么关闭;
  资产系统也永远不关心节点怎么编排调用。**两端都只依赖协议,不依赖实现。**

## 2. Node 的职责

Node 是图执行系统中的计算与编排单位,但 Node 本身不是复杂外部能力的实现单位。

Node 主要负责:

1. 接收输入数据;
2. 接收控制事件与触发信号;
3. 维护自身运行状态;
4. 决定当前事件应该如何处理;
5. 调用所声明的 Asset Capability;
6. 根据能力调用结果产生新的数据、状态变化或事件;
7. 将事件传播给图中的其他节点。

因此 Node 的核心职责是:

```text
事件进入
    ↓
Node 判断当前状态
    ↓
调用 Asset Capability
    ↓
处理结果
    ↓
产生新的事件 / 数据 / 状态
```

Node 不应该承担以下职责:

```text
创建数据库连接
创建 HTTP Client
加载模型
启动浏览器
打开文件
创建 GPU Context
建立网络连接
管理资源缓存
关闭外部资源
```

这些都属于 Asset 或 Asset Management System 的职责。

换句话说,Node 不应该知道"能力是怎么实现的",只应该知道"我可以通过什么协议使用这个能力"。

## 3. Asset 的职责

Asset 是注入 Node 的能力实现实例。

Asset 不等价于简单的数据对象,也不等价于资源文件。Asset 的核心语义是:

> **一个可以被节点通过约定协议调用的能力实例。**

例如:

```python
class Database(Capability):
    def query(self, sql: str) -> list[dict]:
        ...
```

节点只依赖:

```python
database: Database
```

而不依赖具体实现:

```python
SQLiteDatabase
PostgresDatabase
MySQLDatabase
RemoteDatabase
FakeDatabase
NullDatabase
```

因此:

```text
Node
  │
  │ Database Protocol
  ▼
┌─────────────────────────────┐
│ Asset                       │
│                             │
│ SQLiteDatabase              │
│ PostgreSQLDatabase          │
│ RemoteDatabase              │
│ FakeDatabase                │
│ NullDatabase                │
└─────────────────────────────┘
```

对于 Node 而言,这些实现只要满足相同的 Capability Protocol,就具有相同的使用方式。

## 4. Capability Protocol

Node 与 Asset 之间唯一稳定的运行时边界是 Capability Protocol。

Protocol 定义的是:

> **节点允许对资产提出什么请求,以及资产必须提供什么能力。**

Protocol 不定义:

* Asset 如何创建;
* Asset 使用什么底层库;
* Asset 使用什么配置;
* Asset 从哪里获取资源;
* Asset 是否连接远程服务;
* Asset 是否共享;
* Asset 如何释放资源。

例如:

```python
class DatabaseCapability(Protocol):
    def query(self, sql: str) -> list[dict]:
        ...
```

Node 只可以依赖该协议:

```python
database = ctx.assets["database"]

rows = database.query(
    "SELECT * FROM users"
)
```

Node 不应该进行以下操作:

```python
database.connection
database.config
database.close()
database.reconnect()
database.pool
```

因为这些都突破了 Capability Protocol 的边界。

协议稳定性:Capability 协议是**节点包生态的稳定边界**,与主内核
node-protocol.md 的 ABI 同地位——能力接口的变更影响所有消费它的节点,
资产系统内部实现的变化对节点完全不可见。协议用 `runtime_checkable
Protocol` 表达,构建期 `isinstance` 校验即协议一致性的执行点。

## 5. Capability 是 Node 与 Asset 的契约

AssetIn 描述 Node 对 Capability 的依赖:

```python
@dataclass(frozen=True)
class AssetIn:
    name: str
    type: type | None
```

例如:

```python
NodeType(
    name="UserQuery",
    asset_in=(
        AssetIn(
            name="database",
            type=DatabaseCapability,
        ),
    ),
)
```

这表示:

> UserQuery 节点运行所需要的 `database` 能力必须由外部注入,并且注入对象必须满足 `DatabaseCapability`。

这里的 AssetIn 不是资产本身,也不是资产实例。

它只是 Node 对外表达的能力依赖声明。

因此三者必须严格区分:

```text
AssetIn
    ↓
"我需要什么能力"

AssetRef
    ↓
"我要使用哪个资产实例"

Asset
    ↓
"这个能力具体如何实现"
```

## 6. 声明即必须

所有声明出来的 AssetIn 都属于 Node 的运行前置条件。

因此:

> **Node 声明了 AssetIn,就必须在 GraphInstance 构建完成之前获得对应的有效 Capability。**

不存在:

```text
AssetIn → None
```

这样的正常运行状态。

构建阶段必须完成:

```text
AssetIn
  ↓
AssetRef
  ↓
AssetResolver
  ↓
Asset Instance
  ↓
Capability 类型验证
  ↓
注入 GraphInstance
```

任何一步失败,都属于 Build Error。

例如:

```text
未绑定资产
        ↓
Build Error

asset_id 不存在
        ↓
Build Error

资产解析失败
        ↓
Build Error

资产类型错误
        ↓
Build Error
```

运行时不再承担"资产是否存在"的职责。

这保证了 Node 的运行代码可以建立一个更强的不变量:

```python
ctx.assets["database"]
```

返回的永远是满足协议的 Capability,而不是:

```python
Capability | None
```

因此节点不需要为资产不存在编写额外的分支逻辑。

## 7. 降级能力属于 Asset System

当业务需要"可选能力"或者"能力不可用时降级"时,不应重新引入 `None` 语义。

例如数据库不可用时,可以由 Asset Management System 提供:

```text
DatabaseCapability
        │
        ├── PostgreSQLDatabase
        ├── SQLiteDatabase
        ├── RemoteDatabase
        └── NullDatabase
```

Node 始终获得:

```python
DatabaseCapability
```

而不是:

```python
DatabaseCapability | None
```

例如:

```python
class NullDatabase:
    def query(self, sql: str) -> list[dict]:
        return []
```

于是:

```text
真实数据库
    ↓
DatabaseCapability

无数据库环境
    ↓
NullDatabase
    ↓
DatabaseCapability
```

降级逻辑因此被集中到 Asset Management System,而不是扩散到所有依赖该能力的 Node。

这意味着:

> **可选的是具体实现,而不是 Capability 依赖本身。**

## 8. AssetRef 与 Asset Instance

Node 在 GraphDefinition 中不保存 Asset 实例,而只保存 AssetRef:

```python
@dataclass(frozen=True)
class AssetRef:
    asset_id: str
```

AssetRef 只表达:

> "这个槽位应该使用哪个资产身份。"

它不包含:

* 创建参数;
* 连接参数;
* 生命周期信息;
* 运行时对象;
* 具体实现类型;
* 底层资源。

因此:

```text
GraphDefinition
    │
    └── AssetRef("main_database")

Asset Management System
    │
    └── "main_database"
          ↓
      Database Instance
```

Graph 只保存依赖关系,不保存能力实例本身。

## 9. Asset Management System 的职责

Asset Management System 是所有 Asset 实例的管理者。

它负责:

1. 解析 AssetRef;
2. 根据 AssetRef 找到资产;
3. 根据配置创建资产;
4. 管理资产初始化参数;
5. 决定资产实例是否共享;
6. 创建独立资产实例;
7. 管理资产状态;
8. 处理资产失效与恢复;
9. 管理资产生命周期;
10. 最终销毁资产实例。

其核心接口可以抽象为:

```python
class AssetResolver(Protocol):
    def resolve(self, ref: AssetRef) -> Any:  # 返回使用面投影(Capability)
        ...
```

Graph Runtime 不需要知道资产如何被创建。

例如:

```text
GraphInstance
    │
    │ resolve(AssetRef("main_db"))
    ▼
Asset Management System
    │
    ├── 查找配置
    ├── 检查实例
    ├── 创建 / 获取实例
    ├── 初始化
    └── 返回 Capability
```

Graph Runtime 只获得最终的 Capability。

### 9.1 管理协议:资产暴露给资产管理系统的表面

与 §4 的使用协议(节点 ↔ 资产)相对,资产还有一套**只对资产系统可见**的
管理表面:

```python
class AssetFactory(Protocol):
    """资产种类注册:参数 → 实例。"""
    def create(self, **params) -> "ManagedAsset": ...

class ManagedAsset(Protocol):
    """实例管理面:生命周期。节点永远看不到这个表面。"""
    def health(self) -> bool: ...
    def reconnect(self) -> None: ...
    def close(self) -> None: ...
```

| 阶段 | 协议操作 | 语义 |
|---|---|---|
| 注册 | `register(kind, factory)` | 资产种类:参数 → 实例的构造器 |
| 创建 | `create(kind, **params) → AssetRef` | 实例化;创建参数只出现在这里 |
| 运行 | `health()` | 健康探测(由资产系统发起) |
| 恢复 | `reconnect()` | 默认恢复语义:实例内部重连,能力对象身份不变 |
| 销毁 | `close()` | 只有资产系统能调用 |

最小验证内核中的对应形态:`tests/fake_assets.py` 的 FakeAssetSystem 即
此协议的假实现——使用面 `query()` / `get()`,管理面
`fail() / recover() / destroy()`(重连 = 置回 failed 位,对象身份不变),
`FakeNullDatabase` 即环境降级的 Null 资产(§7)。

## 10. Asset Management System 是实例所有者

资产的所有权属于 Asset Management System,而不是 Node 或 GraphInstance。

因此:

```text
GraphInstance
    │
    └── 引用 ────────┐
                     ▼
              Asset Instance
                     ▲
                     │
              Asset System
                 owns it
```

GraphInstance 被销毁时:

```python
del graph_instance
```

只能释放自己的引用。

它不应该:

```python
asset.close()
asset.destroy()
asset.dispose()
```

因为 GraphInstance 并不拥有 Asset。

同一个 Asset 被多个 GraphInstance 使用时,这一点尤其重要:

```text
Graph A ─────┐
             │
Graph B ─────┼──→ Asset X
             │
Graph C ─────┘
```

任何一个 GraphInstance 的销毁都不能导致 Asset X 被销毁。

只有 Asset Management System 才可以决定 Asset X 的生命周期。

## 11. 独立资产与共享资产

Asset Management System 可以根据自身策略决定返回独立实例还是共享实例。

例如:

```text
AssetRef("db-main")
AssetRef("db-main")
AssetRef("db-main")
        │
        ▼
同一个 Database Instance
```

多个节点可以共享同一 Asset。

也可以:

```text
AssetRef("worker-a")
AssetRef("worker-b")
        │
        ├── Worker Instance A
        └── Worker Instance B
```

获得两个完全独立的实例。

这种区别不应该进入 Node 的业务逻辑。

Node 只知道:

```python
ctx.assets["worker"]
```

可以调用 Worker Capability。

它不需要知道:

```text
这是共享实例
```

还是:

```text
这是独立实例
```

实例拓扑属于 Asset Management System 的管理策略。

**调用顺序裁定(2026-08-20):** 共享实例的调用顺序**不构成 Runtime 语义**——
内核不承诺、不检查声明序。若共享可变资产因调用顺序不同产生不同结果,属
节点编排问题与资产可共享性声明问题:是否允许共享、共享下的顺序约束,
由 Asset Management System(可共享性声明)与编排节点负责,内核不承担。

## 12. 创建参数与 AssetRef 必须分离

AssetRef 只表示资产身份,而资产创建参数属于 Asset Management System。

例如:

```python
AssetRef("llm-primary")
```

不应该包含:

```python
{
    "model": "xxx",
    "temperature": 0.7,
    "api_key": "...",
    "endpoint": "...",
}
```

这些参数属于资产系统。

因此可以形成:

```text
AssetRef
    │
    ▼
Asset Management System
    │
    ├── asset_id
    ├── implementation
    ├── initialization parameters
    ├── credentials
    ├── pooling policy
    └── lifecycle policy
            │
            ▼
       Asset Instance
```

这样 Graph 可以保持纯粹的结构描述,而不会把运行环境配置泄漏进图定义。

## 13. Node 与 Asset 的调用边界

Node 可以:

```text
读取 Capability
调用 Capability
处理 Capability 返回结果
根据结果继续执行
```

Node 不可以:

```text
创建 Capability
销毁 Capability
修改 Asset 生命周期
读取 Asset 私有配置
直接访问 Asset 内部实现
```

因此正确的关系是:

```python
result = ctx.assets["database"].query(sql)
```

而不是:

```python
db = Database(...)
```

更不是:

```python
ctx.assets["database"].close()
```

Node 是 Capability 的使用者,而不是 Capability 的管理者。

## 14. Asset 的内部实现完全独立于 Node

Asset 可以脱离 Node 单独测试。

例如:

```text
DatabaseAsset
    │
    ├── query()
    ├── execute()
    └── transaction()
```

可以直接进行:

```text
Asset Unit Test
    ↓
DatabaseCapability
    ↓
Database Implementation
```

而不需要构造 Graph、Node、事件传播链或者 Runtime。

同样,Node 可以使用 Fake Asset:

```text
UserQuery Node
      │
      ▼
FakeDatabase
```

从而独立验证 Node 的事件编排逻辑。

于是测试可以明确分成两类:

```text
Asset Test
    验证能力是否正确

Node Test
    验证事件编排是否正确

Integration Test
    验证 Node + Asset Protocol 是否正确协作
```

这使复杂能力与事件执行逻辑真正解耦。

## 15. Graph Runtime 与 Asset 不形成控制关系

Graph Runtime 的核心职责仍然是:

```text
Injection
    ↓
Event Propagation
    ↓
Node Activation
    ↓
Node Execution
    ↓
State Update
    ↓
New Event
    ↓
Propagation
```

Asset 调用发生在 Node 执行内部:

```text
Graph Runtime
      │
      ▼
    Node
      │
      ▼
Asset Capability
      │
      ▼
    Result
      │
      ▼
    Node
      │
      ▼
New Event / State
```

因此 Asset 不应该主动驱动 Graph Runtime。

Asset 可以执行复杂工作,但它的返回结果必须通过 Node 转化为 Graph Runtime 可以理解的结果。

这样可以保持运行模型的单向控制关系:

```text
Runtime → Node → Asset
```

而不是:

```text
Runtime ↔ Node ↔ Asset
```

避免 Asset 反向控制图执行器。

## 16. Asset 可以很复杂,Node 可以很简单

这是该协议最重要的架构收益之一。

一个 Node 可以只负责:

```text
收到事件
    ↓
读取输入
    ↓
调用 Capability
    ↓
根据结果产生事件
```

而 Asset 内部可以包含非常复杂的实现:

```text
LLM Asset
    ├── Prompt 编译
    ├── Context 管理
    ├── API 调用
    ├── Streaming
    ├── Tool Calling
    ├── Retry
    ├── Cache
    ├── Rate Limit
    └── Provider Adapter
```

Node 不需要知道这些实现细节。

同样,一个 Browser Asset 可以内部包含:

```text
Browser
    ├── Process
    ├── CDP
    ├── Page
    ├── Cookie
    ├── Network
    ├── Screenshot
    └── DOM
```

Node 只看到:

```python
browser.navigate(url)
browser.click(selector)
browser.get_text(selector)
```

这使复杂能力可以作为独立的软件模块存在。

## 17. Asset 是可替换的实现边界

只要多个 Asset 满足同一个 Capability Protocol,就可以互换。

例如:

```text
DatabaseCapability
        │
        ├── SQLiteAsset
        ├── PostgreSQLAsset
        ├── MySQLAsset
        ├── MockDatabase
        └── NullDatabase
```

Node 不需要修改。

因此:

```text
Node
  +
Capability Protocol
```

构成稳定接口,而:

```text
Asset Implementation
```

成为可替换实现。

这使 Eidolon 的运行图天然具备依赖注入能力。

## 18. Asset 不属于 Graph

Graph 只描述:

```text
Node
Connection
Binding
AssetRef
```

而不描述:

```text
Asset Instance
Asset Lifecycle
Asset Runtime State
```

因此:

```text
GraphDefinition
        │
        ├── Node definitions
        ├── Connections
        └── AssetRef
```

与:

```text
Asset Management System
        │
        ├── Asset definitions
        ├── Initialization parameters
        ├── Asset instances
        └── Lifecycle
```

保持独立。

Graph 可以被复制、保存、加载、编辑,而不需要复制 Asset 实例。

## 19. 运行时资产注入

GraphInstance 构建阶段完成资产解析:

```python
GraphInstance.build(
    definition,
    types,
    asset_resolver=resolver,
)
```

构建过程:

```text
GraphDefinition
      │
      ▼
结构验证
      │
      ▼
读取 NodeType.asset_in
      │
      ▼
读取 Graph AssetRef
      │
      ▼
AssetResolver.resolve()
      │
      ▼
Capability 类型验证
      │
      ▼
注入 GraphInstance.assets
```

最终形成:

```python
instance.assets[node_id][slot_name] = capability
```

Node 执行时只访问已经解析完成的 Capability。

因此执行阶段不再承担资产解析。

## 20. 构建期不变量

GraphInstance 一旦成功构建,就必须满足:

```text
∀ Node
    ∀ AssetIn
        存在对应 AssetRef
        ∧ AssetRef 可以解析
        ∧ resolved asset 满足 Capability Protocol
```

换句话说:

```python
build(...).ok == True
```

意味着:

```python
ctx.assets[slot]
```

一定不是:

```python
None
```

也不会是:

```python
wrong_type
```

资产依赖问题必须在 Build 阶段全部解决。

运行阶段可以发生资产内部故障,但这属于另一种错误:

```text
Build Error
    = 能力不存在 / 能力类型错误

Runtime Asset Error
    = 能力存在,但执行失败 / 暂时失效
```

两者不能混淆。

## 21. Asset 运行期失效

Asset 在构建成功之后仍然可能发生运行期错误。

例如:

```text
Database
    ↓
网络断开

LLM
    ↓
Provider unavailable

Browser
    ↓
Browser process crashed

GPU
    ↓
Device lost
```

这并不违反构建期不变量。

因为:

```text
Capability 存在
```

与:

```text
Capability 当前调用成功
```

是两个不同的问题。

因此:

```text
Build
    保证"能力存在且类型正确"

Runtime
    处理"能力调用是否成功"
```

资产运行期失败可以通过 Runtime 已有的错误传播机制进入:

```text
TickContext
    ↓
KIND_ERROR
```

但不应伪装成"资产不存在"。

**失效恢复裁定:** 默认恢复 = 资产系统经管理面调用 `reconnect()`,能力
对象**身份不变**——节点持有的引用天然继续有效,无需任何失效处理。若必须
整体替换实例(进程级损坏),替换是资产系统内部实现:注入节点的能力投影
必须保持有效(稳定代理),Graph 依旧无感知。恢复后下一 epoch 正常执行。

## 22. Asset System 与 Runtime 的边界

Asset Management System 可以管理:

```text
创建
初始化
配置
缓存
共享
连接池
失效
恢复
销毁
```

Runtime 可以管理:

```text
Graph
Node
Event
Signal
Data
State
Propagation
Execution
```

两者之间只有必要的协议连接:

```text
Runtime
    │
    └── AssetResolver
             │
             ▼
      Asset Management System
             │
             ▼
         Capability
```

Runtime 不需要知道 Asset System 的内部结构。

Asset System 也不需要知道 Graph 的拓扑结构。

## 23. 三层协议的最终结构

Eidolon 的能力体系最终形成三个不同方向的协议。

### Node ↔ Graph Runtime

定义:

```text
如何运行节点
如何传播事件
如何更新状态
如何产生输出
```

这是**执行协议**。

### Node ↔ Asset

定义:

```text
节点需要什么能力
节点可以调用什么方法
调用产生什么结果
```

这是**能力使用协议**。

### Asset ↔ Asset Management System

定义:

```text
如何创建资产
如何初始化资产
如何配置资产
如何共享资产
如何管理资产生命周期
```

这是**资产管理协议**。

三个协议互不替代:

```text
                Graph Runtime
                     │
                Execution
                     │
                     ▼
                   Node
                     │
               Capability
                     │
                     ▼
                   Asset
                     ▲
                     │
             Asset Management
```

## 24. 最终职责边界

最终裁定如下。

| 对象                      | 核心职责                      | 不负责                      |
| ----------------------- | ------------------------- | ------------------------ |
| Graph Runtime           | 执行图、传播事件、调度 Node、更新运行状态   | Asset 创建与生命周期            |
| Node                    | 编排输入、状态、事件,并调用 Capability | 复杂能力实现、Asset 生命周期        |
| Asset                   | 提供具体 Capability 实现        | Graph 调度、Node 编排、生命周期所有权 |
| Asset Management System | 创建、配置、共享、解析、管理 Asset      | Graph 事件传播与 Node 执行      |

可以进一步压缩成四句话:

> **Runtime 决定什么时候运行。**

> **Node 决定运行时如何编排。**

> **Asset 决定具体能力如何实现。**

> **Asset Management System 决定能力实例如何存在。**

## 25. 架构意义

这一协议使 Node 从"能力实现对象"进一步收敛为"执行图中的行为编排对象"。

此前如果复杂能力直接封装在 Node 内部,那么:

```text
Node
 ├── Event Logic
 ├── State Logic
 ├── Database Implementation
 ├── HTTP Implementation
 ├── LLM Implementation
 └── Resource Lifecycle
```

Node 会同时承担多个完全不同的职责。

新的架构则变为:

```text
Node
 ├── Event Logic
 ├── State Logic
 └── Capability Calls
          │
          ├── Database Asset
          ├── HTTP Asset
          ├── LLM Asset
          ├── Browser Asset
          └── ...
```

因此 Node 的复杂度主要来自图行为与事件编排,而不是来自它所使用的具体能力。

与此同时,复杂能力可以作为独立的软件模块开发、测试、复用和替换。

这意味着 Eidolon 的 Node 不再是能力的最终封装边界,而成为能力的**组合与编排边界**。

与旧模型(主内核 node-protocol.md)的对照:

| | 旧模型 | 新模型(本文档) |
|---|---|---|
| 能力实现归属 | 节点包内部(`execute()` 写 OpenAI / DeepSeek / Agent Loop) | 注入的资产实例(LLM 客户端、数据库连接、GPU 句柄…) |
| 节点职责 | 声明 + 状态 + 执行逻辑 + 资源 + 配置 | 纯编排:事件传播逻辑、何时 / 以何条件 / 以何顺序调用能力 |
| 能力复用单位 | 节点(能力 + 编排一起复用) | 资产(能力)与节点(编排)各自独立复用 |
| 调试单元 | 能力不可脱离节点测试 | 资产独立实例化、独立调试;节点注入假资产独立测试 |

## 26. 最终原则

Eidolon 的 Asset 架构遵循以下原则:

1. **资产是能力实现,不是节点的内部组成部分。**
2. **Node 通过 Capability Protocol 使用 Asset,而不依赖具体实现。**
3. **AssetIn 是能力依赖声明,不是资产实例。**
4. **AssetRef 是资产身份引用,不包含创建参数。**
5. **声明即必须,成功构建的 Node 不存在缺失 Asset 的正常状态。**
6. **降级通过真实的 Capability 实现完成,而不是通过 `None`。**
7. **Asset 的创建与生命周期属于 Asset Management System。**
8. **GraphInstance 只持有 Asset 引用,不拥有 Asset。**
9. **多个 Node 或 GraphInstance 可以共享同一个 Asset。**
10. **Asset 可以脱离 Node 独立测试。**
11. **Node 可以使用 Fake / Mock / Null Asset 独立测试。**
12. **Runtime 负责执行与传播,Asset 不反向控制 Graph Runtime。**
13. **构建期验证资产依赖是否成立,运行期只处理资产调用是否成功。**
14. **Node 与 Asset 之间的协议是稳定边界,Asset 的具体实现可以自由替换。**
15. **State/Data/Event 载荷的值域是 Value;Capability 不得进入任何传播/状态平面(内核以可复制性探针校验,违者拒绝;2026-08-20 裁定)。**

最终,Eidolon 的核心执行模型可以表示为:

```text
                 ┌─────────────────────┐
                 │    Asset System     │
                 │                     │
                 │ create / configure  │
                 │ share / resolve     │
                 │ lifecycle           │
                 └──────────┬──────────┘
                            │
                         Asset
                            │
                    Capability Protocol
                            │
                            ▼
┌────────────────────────────────────────────────┐
│                  Graph Runtime                 │
│                                                │
│   Event → Node → Capability → Result → Event  │
│            │                                   │
│            └──── State / Data / Signal ────────┤
│                                                │
└────────────────────────────────────────────────┘
```

**核心边界最终收敛为:Graph Runtime 管执行,Node 管编排,Asset 管能力,Asset Management System 管实例。**

## 27. 已验证边界(进入主内核前)

以假 AssetSystem(现有 `tests/fake_assets.py`)验证以下边界。验证结果:
**全部通过**(2026-08-20,`tests/test_assets.py` 17 例),本协议随 Asset 层
一并迁移主内核:

1. 使用面不含管理操作:Capability Protocol 无 close / health,构建期类型
   检查即执行点(graph-assets.md §9 测试间接覆盖);
2. 管理面只对资产系统:Graph Runtime 无任何管理调用路径——实例销毁不
   close(graph-assets.md §9-5 已验);
3. 独立 / 共享引用(§9-1 / 2 已验);
4. 失效恢复默认重连语义:能力对象身份不变,节点无感知(§9-6 已验);
5. 资产独立调试:`create` → 直接调用使用面 → 断言,不经任何图(§14);
6. 节点独立调试:注入假资产验证编排,替换真实资产零改动(§14);
7. 协议表面分离:节点契约上不含管理方法(Protocol 未声明);反射绕过属
   契约外行为,不防御(graph-assets.md §2-5 信任模型);
8. 单向控制:Asset 不反向驱动 Graph Runtime(§15);
9. 值域边界:状态提交/数据产出/宿主注入的可复制性探针,不可复制 →
   KIND_ERROR / ValueError(2026-08-20 裁定);
10. 调用顺序不构成语义:内核不承诺声明序,顺序敏感性归资产系统与编排
    (§11,2026-08-20 裁定)。
