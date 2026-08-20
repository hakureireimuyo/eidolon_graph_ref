# Asset 模型(运行时资源平面)

> 状态:已裁定 + 已验证(2026-08-20,§9 七条边界全部通过;裁定修订:声明即必须)
>
> 定位:本文档锁定 Asset 层的语义裁定。最小验证内核以假 AssetSystem
> 验证边界(见 §9)已全部通过,待迁移主内核。
>
> 相关:[节点 ↔ 资产 ↔ 资产管理系统协议](./graph-asset-protocols.md)——
> Asset 层内部的三方协议(使用面 / 管理面)。

## 1. 背景与动机

节点目前只有 `config` / `state` 两类数据,没有运行时资源概念。但数据库查询
节点需要数据库连接、LLM 节点需要调度客户端——这些外部运行时对象需要在节点
内跨执行持有。

关键前提:**编辑与运行分离**。编辑期图定义是纯描述(如同写代码,只写符号名),
不持有任何活对象;运行期才进行绑定检查、类型验证与对象提供:

```text
编辑期:声明依赖
构建期:解析依赖 + 类型验证 + 能力绑定
运行期:使用依赖
```

资产本身早已由 Asset System 创建和管理。

## 2. 核心原则(六条,已锁定)

1. **资产由独立的 Asset System 创建和拥有。** Node 与 Graph Runtime 均不负责
   资产生命周期(创建/初始化/保存/共享/销毁都在资产系统)。
2. **Graph 只保存 AssetRef,不保存运行时对象。** GraphDefinition 保持可序列化、
   可编辑、可验证、可持久化——它不会退化成"带活对象的运行环境"。
3. **相同参数不意味着相同资产。** Asset System 可以创建任意多个独立实例,
   实例身份独立于创建参数。`Asset #42 ≠ Asset #43` 即使参数完全相同。
4. **共享是自然引用关系,而不是特殊模式。** 多个节点引用同一 `asset_id` 即
   共享;需要独立实例时,由使用者创建多个资产并分别绑定。不存在 `shared`
   标志——共享关系天然存在于 AssetRef 的图结构中。
5. **节点只有使用权,没有所有权。** 节点不能关闭、销毁或管理资产生命周期。
   执行点:AssetIn 声明的类型就是 Capability 接口(不含 `close()` 等管理
   操作),构建期类型检查即不变量执行点。用反射绕过属契约外行为,不防御
   (与"共享载荷引用 / 节点不得原地修改输入"同一信任模型)。
6. **Runtime 只负责依赖解析和能力提供。** Event / Readiness / pending /
   NodeTurn / Epoch 等执行语义完全不知道 Asset 的存在;Asset 不产生任何事件。

## 3. 概念收敛

| 概念 | 定义 | 层 |
|---|---|---|
| `AssetRef` | GraphDefinition 中的纯身份引用(`asset_id`) | 编辑期数据 |
| `Asset` | Asset System 所拥有的运行时实例 | 资产系统 |
| `Capability` | Node 获得的受限使用接口(消费者概念) | 运行期 |
| `AssetIn` | NodeType 对 Capability 的依赖声明 | 声明 |
| `AssetBinding` | GraphDefinition 对具体 AssetRef 的绑定 | 编辑期数据 |
| `GraphInstance` | resolve AssetRef → Capability | 运行期 |
| `Node` | 只能使用 Capability,不拥有 Asset | 运行期 |

"需要什么 → 使用哪个 → 实际是什么对象" 三个层次分别对应:

```text
NodeType          声明需要什么 Asset      asset_in = AssetIn("llm", LLMCapability)
GraphDefinition   指定使用哪个 AssetRef   bind_asset(node="writer", slot="llm", asset_id="llm-42")
GraphInstance     解析成什么对象          resolve("llm-42") → LLMCapability → ctx.assets["llm"]
```

Capability 是安全边界:资产系统内部可以持有 client / connection / scheduler /
lifecycle 等全部实现,节点只通过 Capability 接口(如 `generate()`)使用它,根本
不知道资产究竟是什么。多个节点甚至可拿到同一个 Capability,而其背后的资产
实例仍由 Asset System 管理。

## 4. 三个正交平面

```text
                    Node
                     │
       ┌─────────────┼─────────────┐
       │             │             │
       ▼             ▼             ▼
  State Plane    Event Plane    Resource Plane
       │             │             │
   state/config   Data/Signal   Asset/Capability
       │             │             │
   持久事实        传播事实       外部资源
```

| 平面 | 代表 | 生命周期 | 是否传播 |
|---|---|---|---|
| State | `state` / `config` | Node Instance | 否 |
| Event | Data / Signal | Event | 是 |
| Resource | Asset / Capability | Asset System | 否 |

**资产会影响节点执行,但它本身不是执行传播事实。** 绝不能因为"资产影响执行"
就把资产塞进 Event 或 State——否则资产系统与事件系统重新耦合,重蹈职责混合。

对称关系:`DataIn` 接收动态传播的 Event(传播平面),`AssetIn` 接收静态绑定的
Runtime Capability(资源平面)。二者都是节点的输入依赖,但属于不同平面。

## 5. 生命周期与编辑/运行分界

```text
编辑期(纯描述:Node/Port/Group/Binding/AssetRef/Config)
  — 没有 socket、数据库连接、HTTP 会话、LLM 客户端、GPU 句柄

构建期(GraphDefinition → 结构验证 → AssetRef 解析 → 类型验证 → GraphInstance)
  — 此时才出现 writer.assets["llm"] = capability

执行期(Node 只使用 ctx.assets["llm"])
```

完整流程与编程语言同构:

```text
source → parse → type check → link → initialize → execute
编辑   → 结构验证 → 类型验证  → 解析  → 提供能力    → 执行
```

**GraphDefinition 是"程序",GraphInstance 是"程序的一次运行"。**

## 6. 错误分层

| 错误 | 层级 | 语义 |
|---|---|---|
| 资产初始化失败(连接建立失败) | 资产系统 | 发生在 Graph 构建之前;资产系统内部重试/降级/替换,Graph 不可见 |
| 解析失败 / 类型不符 / 必需资产未绑定 | 构建期 | BuildReport error;不存在可继续 `run()` 的实例;不进 KIND_ERROR、不进时间线 |
| 运行期资产失效(tick 内调用失败) | 执行期 | 普通 tick 异常 → 既有 KIND_ERROR 语义;恢复/替换由资产系统负责,Graph 不感知 |
| 释放 | 资产系统 | GraphInstance 销毁仅释放自身引用,不调用任何 `close()` |

构建期错误与 tick 异常必须严格分层:tick 异常是"一个已经运行起来的节点这次
执行失败",资产构建失败是"这个 Runtime 根本没有满足运行前提"。

## 7. 关键裁定记录

| 问题 | 裁定 |
|---|---|
| 资产槽位未绑定 | **声明即必须**(2026-08-20 修订,替代原"可选 → None"裁定):声明的槽位构建期必须绑定且解析成功,否则 BuildReport error——资产是资源而非数据,缺席是结构缺陷,不允许"运行时才发现"。降级需求由资产系统提供 Null 资产(真实 Capability),内核永不出 None 槽位 |
| 构建错误形态 | `BuildReport` 一次性收集全部错误(资产依赖多,逐个报错会让宿主反复启动);`ok=False` 时**不存在** GraphInstance。API 为 `result = GraphInstance.build(...)`,禁止"构造半成品再 try resolve" |
| 绑定归属 | **GraphDefinition**(编辑期纯数据)。NodeType 声明"需要什么",图指定"使用哪个",实例解析"实际是什么"。类型声明不应知道具体运行环境的资产身份 |
| AssetRef 内容 | 仅 `asset_id`(实例身份),不含创建参数。参数属于资产系统创建时的配置 |
| 共享 / 独立 | 不由 Runtime 强制,完全由 AssetRef 指向哪个 `asset_id` 决定 |
| 快照 | 只含 Graph 引用 + Node State + 执行状态 + AssetRef。恢复 = 恢复逻辑状态 + 对**当前**资产系统重新解析,绝不恢复旧资产对象 |
| 资产失效 | 不自动变成 Signal/Data/Trigger Event。节点调用失败 → 异常 → KIND_ERROR;资产系统后台断线重连恢复,Graph 不需要知道 |
| 注入时机 | 资产在 Graph 构建前已由资产系统创建,构建期 lookup 即 eager——懒解析没有意义 |
| State/Data 值域 | **Value 与 Capability 分属不同语义类别**(2026-08-20 裁定):State/Data/Event 载荷的值域 = Value(可复制/可序列化),Capability 不得进入任何传播/状态平面。执行点:状态提交、数据产出、宿主注入三入口 deepcopy **探针**校验(只校验不复制,数据平面保持零拷贝),失败 → 状态/产出 KIND_ERROR + 拒绝提交、注入 ValueError。判据是可复制性——恰好可复制的"伪能力"对象属契约外,与反射绕过同一信任模型 |

## 8. API 形状(草案)

```python
# model/assets.py
@dataclass(frozen=True)
class AssetIn:
    name: str
    type: type | None = None   # Capability 接口(类或 runtime_checkable Protocol)
                               # 声明即必须:构建期必须绑定并解析成功(§7 裁定)

@dataclass(frozen=True)
class AssetRef:
    asset_id: str              # 实例身份,不是参数

# NodeType 增加声明维度(与 data_in/trigger_in/signal_in 并列)
asset_in: tuple[AssetIn, ...] = ()

# GraphDefinition 只存引用
g.bind_asset("db_query", "database", "main_db")
# → asset_bindings[("db_query", "database")] = AssetRef("main_db")

# 运行期:宿主传入解析函数(资产系统的客户端)
class AssetResolver(Protocol):
    def resolve(self, ref: AssetRef) -> Any: ...

result = GraphInstance.build(g, types, asset_resolver=host_resolver)
if not result.ok:
    print(result.errors)          # 全部错误;result.instance is None
world = result.instance

# tick 访问;键集合 = 声明集合;tick 不可写资产
ctx.assets["database"]            # 构建期已解析成功(声明即必须),恒为 Capability

# observable_state 只暴露结构事实,绝不暴露对象
"assets": {"database": {"ref": "main_db", "resolved": True}}
```

实现要点:

- `instance.assets[nid] = {slot: capability}` —— 单独 store,不进 `node_states`,
  不 deepcopy(不可序列化);快照/回放天然排除资产
- `TickContext` 增加 `assets` 字段,每次 fire 传入该节点的浅拷贝(能力对象共享,
  tick 插入不影响节点 store)
- 编辑期 `validate()` 只检查结构:绑定引用已声明的槽、节点存在、绑定唯一。
  **资产是否存在是运行期问题**(目录在资产系统里),编辑期不检查
- 构建期解析:逐节点按声明序 lookup → `isinstance` 校验 → 注入;错误收集成
  报告一次性返回,不留半构建实例

## 9. 已验证边界(进入主内核前)

以假 `AssetSystem + AssetResolver + Capability` 实现验证以下边界。验证结果:
**全部通过**(2026-08-20,`tests/test_assets.py` 13 例)。

1. 同一 Asset 被多个节点共享(同一 `asset_id` → 同一底层实例)
2. 相同参数创建两个独立 Asset(身份独立于参数)
3. 资产未绑定或缺失 → BuildReport error(声明即必须);降级经资产系统 Null 资产(真实 Capability)
4. 类型错误 → BuildReport error;多错误一次收集
5. GraphInstance 销毁不关闭 Asset(所有权在资产系统)
6. 运行期间 Asset 失效 → tick 异常 / KIND_ERROR,不产生任何传播事件;
   资产系统恢复后下一 epoch 正常执行
7. 快照 / observable_state 不携带 Asset 对象(只含 ref/resolved 结构事实)
