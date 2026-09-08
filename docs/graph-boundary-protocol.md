# Graph Boundary Protocol（外部交互与翻译层）

> 状态:2026-09-08 架构裁定,待实现
>
> 定位:本文档定义 Graph 的外部边界——InputEndpoint / OutputEndpoint 如何声明,
> Translation Layer 如何与 Kernel Event API 对接,以及"外部交互不污染 Event 内核"
> 这一核心不变量。
>
> 相关:[graph-concepts.md](./graph-concepts.md)、[graph-node-protocol.md](./graph-node-protocol.md)、
> [graph-assets.md](./graph-assets.md)。

## 1. 问题背景

内核的事件模型已经足够纯粹:

```text
Event
├── id
├── run
├── kind (DATA | SIGNAL)
├── payload
├── producer (节点 id | None = 宿主注入)
├── port (产出端口名)
├── deliveries
└── consumed_by
```

`producer + port` 描述事件在 Kernel 内部的产生位置;`Wire` 决定它往哪里传播。
整条链路:注入 → 传播 → Readiness → 行为 → 新事件 → 静止,Kernel 不需要知道
"外部"存在。

但当前外部数据进入 Kernel 的唯一路径是 `Injection(node, port, slot, kind, payload)`,
宿主必须知道内部节点 ID 和端口名才能注入——图的内部结构完全暴露给外部。
这违反了一条基本原则:

> **外部系统只能向 Graph 声明的某个 Input Endpoint 发送数据;
> Graph 产生的外部数据只能通过 Graph Output Endpoint 离开 Graph。**

## 2. 三层分离

```text
GraphDefinition（编译时结构）
├── InputEndpoint(name, kind, type, target=(node, port, slot))
├── OutputEndpoint(name, kind, type, source=(node, port))
├── NodeSpec / Wire / AssetBindings

Translation Layer（边界适配）
├── External → InputEvent(endpoint, data) → resolve → Event → _deliver
└── OutputRecord(endpoint, event) ← Boundary Observer 生成

Kernel Runtime（纯事件）
├── Event(id, run, kind, payload, producer, port)
├── Delivery / Wire routing / PortState / Fire / Emit
└── orphan/pending/consumed = 事件自身生命周期
```

**三者各自承担不同职责,互不污染。**

## 3. 核心不变量

| # | 不变量 | 含义 |
|---|--------|------|
| 1 | Event 不认识 Endpoint | `producer/port` 是 Kernel 内部产生位置,不携带外部语义 |
| 2 | orphan ≠ Graph Output | orphan = 无内部 Delivery;Graph Output = 显式声明的边界观察;两者可共存 |
| 3 | Output Endpoint 是观察者 | Boundary Observation,不是 Kernel Delivery |
| 4 | 一次输出可产生多个事件 | 返回 `list[OutputRecord]`,不是 `dict[str, Event]` |
| 5 | Translation Layer 认识 Endpoint | Kernel 只认识 Event |
| 6 | Endpoint 是 GraphDefinition 的结构语义 | 不是 Runtime 事实 |

## 4. GraphDefinition: Endpoint 声明

### 4.1 InputEndpoint

```text
InputEndpoint
├── name: str            # 稳定身份,如 "user_message"
├── kind: Kind           # DATA | SIGNAL
├── data_type: type      # 数据契约(可选,用于编译期类型检查)
└── target: (node, port, slot)  # 内部连接,编译时确定
```

- `name` 是 Graph 的公开输入标识,外部系统只需要知道这个名字
- `target` 是编译时确定的内部路由,外部系统不需要知道
- 同一个 InputEndpoint 在一次 `run()` 中可以被注入多次

### 4.2 OutputEndpoint

```text
OutputEndpoint
├── name: str            # 稳定身份,如 "assistant_message"
├── kind: Kind           # DATA | SIGNAL
├── data_type: type      # 数据契约(可选)
└── source: (node, port) # 内部来源,编译时确定
```

- `source` 声明哪个 `(node, port)` 的产出应该被观察为 Graph Output
- 一个 `(node, port)` 可以同时扇出到内部节点和 Graph Output(共存,不互斥)
- 不存在"没有 Wire 就是 Output"的推断——orphan 保持原有语义

### 4.3 Endpoint 与 Wire 的关系

```text
Node.output
   ├── Wire → Node A (内部传播 = Delivery)
   ├── Wire → Node B (内部传播 = Delivery)
   └── OutputEndpoint → Boundary Observation (外部观察,不产生 Delivery)
```

OutputEndpoint 与内部 Wire 并列但性质不同:Wire 产生 Delivery(内部传播),
OutputEndpoint 产生 OutputRecord(边界观察)。两者共存,互不干扰。
是否声明为 OutputEndpoint,由 GraphDefinition 显式决定,不由 orphan 状态推断。

## 5. Translation Layer: 边界适配

### 5.1 输入路径 (External → Event)

```text
External System
      │
      │ External Input (HTTPRequest / MessageEnvelope / UICommand / ...)
      ▼
Translation Layer
      │
      │ resolve InputEndpoint: "user_message" → (node="chat", port="message", slot=SLOT_DATA)
      ▼
Event(producer=None, kind=DATA, payload="hello", port="message")
      │
      │ _deliver(inst, e, "chat", "message", SLOT_DATA, queue)
      ▼
Kernel Event Propagation
```

Translation Layer 在进入 Kernel 之前完成 endpoint → (node, port, slot) 的映射。
Kernel 收到的是一个普通的 Event,与 Injection 产生的 Event 完全同构。

**注意**:`InputEvent(endpoint, data)` 是 Translation Layer 的内部数据结构,
不是 Kernel 概念。外部输入可以是任何协议(HTTP/RPC/WebSocket/...),
Translation Layer 负责将其转换为 Kernel Event。Kernel 从头到尾只认识 `Event`。

### 5.2 输出路径 (Event → External)

编译阶段,GraphDefinition 的 OutputEndpoint 声明被解析为与 `out_index` 类似的
**编译时边界输出索引**:

```text
output_boundary_index[(nid, port)]
    → ["assistant_message", ...]
```

Runtime 不重新解释 GraphDefinition;Endpoint 关系在编译阶段已经解析完成。

运行时 `_emit()` 的语义:

```text
_emit(inst, nid, g, out.data_out, Kind.DATA, queue, produced)
      │
      │ 创建 Event(producer=nid, port=port)
      │
      ├── out_index[(nid, port)] → Wire → internal Delivery
      │
      └── output_boundary_index[(nid, port)] → OutputRecord
              │
              ├── 存在 → OutputRecord(endpoint="assistant_message", event=e)
              │           → Translation Layer 翻译为 External Protocol
              │
              └── 不存在 → 无边界观察
```

Boundary Observation 不产生 Delivery,不改变 Event 的生命周期。
Event 的 orphan/pending/consumed 状态完全由内部 Delivery 决定。

### 5.3 OutputRecord

```text
OutputRecord
├── endpoint: str   # OutputEndpoint 名称
└── event: Event    # 内核事件(完整生命周期信息)
```

Translation Layer 消费 `OutputRecord`,将 `event.payload` 翻译为外部协议。
`OutputRecord` 不是 Kernel 概念,是 Boundary Observation Layer 的产物。

## 6. Injection 的定位

`Injection` 是测试/验证期的注入手段,不是生产接口。

```text
当前:
  Injection(node, port, slot, kind, payload) → Event → _deliver

正式:
  InputEvent(endpoint, data) → Translation Layer → Event → _deliver
```

两者的 Kernel Event 完全同构——`producer=None`,由 `_deliver` 投递到目标端口。
区别仅在于入口:一个直接指定内部地址,一个通过 Endpoint 映射。

`Injection` 保留为测试工具,不废弃,不升级为生产接口。

## 7. `run()` 的定位

当前 `run(injections)` = 执行测试注入 + 传播到 quiescence。

未来正式外部接口可能需要不同的 API 名称/签名,但 Event 传播机制不变。

`run()` 的返回值演进方向:

```text
当前:  run(injections) → None
未来:  run(inputs) → RunResult(inputs, output_records, timeline)
```

其中 `output_records: list[OutputRecord]` 包含本次 run 中所有 Graph Output 事件。

## 8. orphan 语义保护

`Event.status == "orphan"` 保持原有语义:产出后没有任何 Kernel 内部 Delivery。

OutputEndpoint 不产生 Delivery,因此不改变 Event 的 orphan/pending/consumed 生命周期。

例如:

```text
Node.output
    └── OutputEndpoint("result")
```

产出的 Event 可以同时满足:

```text
Event.status == "orphan"                          (没有内部 Delivery)
OutputRecord(endpoint="result", event=e)          (被边界显式观察)
```

这并不矛盾——orphan 表示该事件没有继续进入图内部传播,OutputRecord 表示它被
Graph Boundary 观察到了。

如果同时存在内部 Wire:

```text
Node.output
    ├── Wire → Node A
    └── OutputEndpoint("result")
```

则 Event 具有正常 Delivery,status 按原有 Delivery 消费状态计算;
Boundary Observation 不参与该生命周期判断。

因此:

```text
orphan ≠ Graph Output
OutputEndpoint 也不改变 orphan 的定义。
```

"程序员忘记接线"和"有意输出到 Graph Boundary"不应混为一谈。

## 9. 完整数据流示例

### 9.1 一次 LLM 对话的完整流程

```text
GraphDefinition:
  InputEndpoint("user_message", DATA, String, target=("llm", "prompt", SLOT_DATA))
  OutputEndpoint("llm_request", DATA, LLMRequest, source=("llm", "request"))
  OutputEndpoint("assistant_message", DATA, String, source=("llm", "response"))

Step 1: 用户输入
  External → InputEvent("user_message", "hello")
  Translation Layer → Event(producer=None, payload="hello") → _deliver("llm", "prompt", SLOT_DATA)
  Kernel: prompt 端口收到事件 → group_ready → fire → 产出 llm_request + 产出 response

Step 2: LLM 请求离开图
  _emit("llm", "request", LLMRequest(...))
  Boundary Observer: ("llm", "request") 是 OutputEndpoint
  → OutputRecord(endpoint="llm_request", event=Event(payload=LLMRequest(...)))
  Translation Layer → 调用 LLM Service

Step 3: LLM 响应重新进入图
  External → InputEvent("llm_response", LLMResponse(...))
  Translation Layer → Event(producer=None, payload=LLMResponse(...)) → _deliver(...)
  Kernel: 继续传播

Step 4: 最终输出
  _emit("llm", "response", "The answer is 42")
  Boundary Observer: ("llm", "response") 是 OutputEndpoint
  → OutputRecord(endpoint="assistant_message", event=Event(payload="The answer is 42"))
  Translation Layer → 返回给 UI
```

### 9.2 Kernel 从头到尾不知道

```text
UserInterface
LLMService
HTTP
WebSocket
用户身份
模型名称
网络状态
```

Kernel 只知道:

```text
"我收到了一个符合某个输入协议的数据"
"我的图产生了一个符合某个输出协议的数据"
```

## 10. 实现优先级

| 阶段 | 内容 | Kernel 改动 |
|------|------|-------------|
| P0 | GraphDefinition 增加 InputEndpoint / OutputEndpoint 声明 | 仅 model 层 |
| P1 | 编译期校验 Endpoint 的 target/source 合法性 | validate.py |
| P2 | Translation Layer 输入: endpoint → Event 映射 | 新模块 |
| P3 | Boundary Observer: OutputEndpoint 捕获 | executor.py 微增 |
| P4 | `run()` 返回 OutputRecord 列表 | instance.py |
| P5 | Translation Layer 输出: OutputRecord → External Protocol | 新模块 |

P0-P1 不改变任何 Kernel 运行时行为。
P2-P4 是 Kernel 边界的最小扩展,Event/Delivery/Wire/Fire/Emit 基本语义不变。
P5 完全在 Kernel 之外。

## 11. 核心原则

> **Graph Boundary 不建立第二套事件系统。**
>
> Endpoint 是图边界的声明语义;Event 是 Kernel 中实际发生的运行时事实;
> Delivery 是 Kernel 内部传播关系;OutputRecord 是边界层对 Kernel 事件的观察结果。
> Translation Layer 只负责在外部协议与这些既有语义之间进行转换。

这个原则保护整个架构免于"为了支持一个新需求,在 Event 上增加一个字段"的路径。

## 12. 最终数据关系

```text
                 GraphDefinition
                       │
          ┌────────────┴────────────┐
          │                         │
   InputEndpoint              OutputEndpoint
          │                         │
      target                    source
          │                         │
          ▼                         ▼
   (node,port,slot)            (node,port)
          │                         │
          │                         │
          ▼                         │
       Event ──────── Kernel ───────┘
          │
          ├── Delivery → Node
          ├── Delivery → Node
          │
          └── Boundary Observation
                    │
                    ▼
               OutputRecord
                    │
                    ▼
             External Protocol
```

`orphan` 位于一条完全独立的轴:

```text
Event
  │
  └── internal Deliveries?
          │
       yes / no
          │
          ▼
pending / consumed / orphan
```

Boundary Observation 不参与这条判断。

四种关系的严格区分:

| 对象 | 回答的问题 |
|------|-----------|
| `InputEndpoint` | 外部输入应该进入图哪里? |
| `OutputEndpoint` | 图中的哪个输出属于公开边界? |
| `Delivery` | Event 接下来传播给哪个内部目标? |
| `OutputRecord` | 哪个 Event 被边界观察到了? |

这四者一旦分开,整个 Boundary Protocol 就不会反过来侵蚀 Kernel。
