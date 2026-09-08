# World Runtime Semantics（世界运行语义裁定）

> 状态:2026-09-08 架构裁定,待实现
>
> 定位:本文档记录 Eidolon 关于"世界如何运行"的完整语义裁定。
> 核心结论:Kernel 不运行世界,它定义世界对事件的反应。
>
> 来源:2026-09-08 架构讨论(6 轮对话)
>
> 相关:[graph-boundary-protocol.md](./graph-boundary-protocol.md)、
> [graph-concepts.md](./graph-concepts.md)、[graph-node-protocol.md](./graph-node-protocol.md)。

## 1. 问题起源

当前 `world.run(injections)` 的语义:

```text
给我一个事件
    ↓
让图处理它
    ↓
处理到当前因果链静止
    ↓
返回
```

这本质上是一次 **event-driven computation**,不是"运行世界"。
`run()` 应该运行世界,而不是代表一次外部事件传播。

## 2. 核心裁定:外部系统自驱动,图内核事件驱动

```text
External System                    Graph Kernel
├─ 时间                            ├─ 图结构
├─ 调度                            ├─ 节点状态
├─ 网络                            ├─ 端口状态
├─ 持久化                          ├─ readiness
├─ 资源                            ├─ 事件传播
├─ 生命周期                        ├─ 节点执行
├─ 策略                            ├─ 状态变更
└─ 事件适配                        └─ 传播轨迹
         │                                │
         │ Input Event                    │
         └───────────────────────────────→│
                                          │
         ┌───────────────────────────────←│
         │ Output Event                   │
```

**内核只接受事件并推进图。**内核不负责:

- 真实时间
- 定时器
- 网络连接
- 重试策略
- 线程和协程
- 外部资源生命周期
- 持久化
- 消息队列
- 业务流程的长期运行
- "世界是否继续活着"

这些逻辑由外部系统负责。

## 3. 内核是状态机,不是运行中的系统

内核的核心语义:

```text
Sₙ + Eₙ → Sₙ₊₁ + Oₙ
```

其中 `S` 是图的运行状态,`E` 是输入事件,`O` 是本次传播产生的边界输出。

内核不需要回答"下一秒应该发生什么？"
内核只需要回答:"如果现在发生了这个事件,图会变成什么状态,并产生什么输出？"

## 4. 世界(World)与图(Graph)的区分

```text
World = 外部系统中的概念
Graph = 内核中的概念
```

当前 `world` 对象实际上承担了两个概念:World + Graph Runtime。
这正是 `world.run()` 语义奇怪的根源。

## 5. "世界自维持"的正确理解

不是:

```text
World 自己拥有一个循环,自己决定什么时候运行
```

而是:

```text
External System 驱动
    ↓
Event 进入 Graph
    ↓
Graph 处理
    ↓
Output 回到 System
    ↓
System 决定下一步
```

"每秒产生一个事件"不是节点偷偷拥有线程,而是:

```text
External System
    │
    │ 1 second elapsed
    ↓
  Timer/Event
    │
    ↓
Graph Kernel
    │
    ↓
 Outputs
    │
    ↓
External System
```

## 6. Event、Task、Output 的区分

```text
Event   = 发生了什么
Task    = 正在做什么(可能持续很长时间)
Output  = 产生了什么结果
```

一个输入可能对应一个持续存在的执行过程:

```text
InputEvent
    ↓
LLM Node
    ↓
等待 30 秒
    ↓
OutputEvent(token1)
OutputEvent(token2)
OutputEvent(completed)
```

## 7. 异步处理:系统级异步,不是内核内部异步

### 两种"异步"

| 类型 | 含义 | 影响 |
|------|------|------|
| 内核内部异步 | Node 内部 `await something` | 扩大 Kernel 边界 |
| 系统级异步 | Graph → Request → External → Response → Graph | 不扩大 Kernel |

**采用系统级异步。**Kernel 不需要 `await` 外部资源。

```text
Graph
 ↓
LLMRequest (Output Event)
 ↓
External System → LLM Service
 ↓
LLMResponse (Input Event)
 ↓
Graph (continue)
```

Kernel 完全不知道 LLM 是什么、网络是什么、等待是什么。
它只知道"现在来了一个 LLMResponse 事件"。

### 等待状态的表达

```text
State:
    waiting(request_id=42)
```

没有线程,没有 Timer,没有 socket,没有 coroutine。
节点状态机:

```text
Processing
    ↓
LLM.Request → Output
    ↓
Waiting (state)
    ↓
LLM.Response → Input
    ↓
Processing
```

## 8. quiescence 的重新定位

```text
Graph Quiescence ≠ World Idle
```

```text
Graph = quiescent
World = running
Scheduler = waiting
```

这恰恰是一个正常状态。quiesce 表示"当前输入事件引发的图内传播已经结束",
不是"世界停止运行"。

## 9. 输入输出必须显式

### 硬约束

> 凡是影响 Graph 行为、离开 Graph、进入 Graph 的东西,都必须能够在
> Graph 的形式结构中找到对应的输入、输出或状态表达。

审查四个问题:

1. 这个数据从谁那里来？
2. 它通过哪个显式输入进入系统？
3. 这个结果要给谁？
4. 它通过哪个显式输出离开系统？

### Node Output ≠ Graph Output

```text
Node.output
    ├── Wire → Node A (内部传播)
    ├── Wire → Node B (内部传播)
    └── OutputEndpoint → External (边界观察)
```

## 10. 节点依赖的三分法

```text
Data Inputs          节点通过显式输入输出交换的东西
State                图自己持有、由事件驱动改变的东西
Capability           节点实现可以使用的外部能力,但其存在和契约必须被声明
```

### Capability 的边界

可以允许:

```text
External System
    │
    │ explicit binding (Capability)
    ▼
Kernel
    Node
    ├── Data Inputs
    ├── Data Outputs
    ├── State
    └── Required Capabilities
```

不允许:

```text
External World
    │
    │ invisible observation
    ▼
  Node
    │
    ▼
changed behavior
```

**外部系统可以决定"这个能力怎么实现",但不能偷偷决定"这个图现在知道了什么"。**

### Resource 状态的分类

```text
Resource Instance
├── Implementation State (connection_pool, tokenizer_cache, ...)
│      └── Kernel 不关心
│
└── Semantic State (conversation_id, counter, ...)
       └── 必须进入 Graph 的显式模型
```

判断标准:不是"Resource 能不能有状态",
而是"这个状态是否影响 Node 的可观察语义"。

## 11. 核心 API 演进方向

### 内核 API

```python
# 派发一个事件,返回 Execution Handle
execution = world.dispatch(event)

# 读取输出流
async for output in world.outputs():
    ...

# 读取状态
state = world.observable_state()
```

### 外部系统 API

```python
while system.running:
    event = system.next_event()
    result = world.dispatch(event)
    system.handle_outputs(result.outputs)
```

### 演进顺序

| 阶段 | 内容 |
|------|------|
| 1 | 把当前执行器改成异步接口 |
| 2 | 支持异步 handler |
| 3 | 增加输出队列 |
| 4 | 增加 subscribe() 作为输出流适配器 |
| 5 | 增加任务取消、超时、并发策略和 correlation ID |

## 12. 最终边界

```text
外部系统
├─ 时间
├─ 调度
├─ 输入队列
├─ 输出消费
├─ 回调管理
└─ 生命周期
         ↓ await dispatch / submit
图内核
├─ 事件传播
├─ 节点状态
├─ readiness
├─ 状态提交
└─ 输出事件生成
```

> **Eidolon Kernel 不运行世界,它定义世界对事件的反应。**

世界本身由外部 System 运行。System 说"现在发生了什么？",Kernel 回答
"那么图中的状态应该如何变化？"Kernel 说"我需要一个 LLM 结果",
System 负责"我去取得这个结果",然后 System 再告诉 Kernel"结果回来了",
Kernel 继续"那么现在状态继续这样变化"。

## 13. 可视化编辑的价值

在这种架构下,编辑器编辑的是:

```text
事件如何传播
状态在哪里保存
什么条件下节点就绪
哪些事件被转换
哪些输出暴露给系统
```

而不是编辑:

```text
线程 / 定时器 / 网络连接 / 协程 / 重试队列
```

图特别适合表达:状态机、事件转换、条件门控、工作流局部逻辑、
数据处理链、控制信号链、反应式 UI 或设备逻辑。

## 14. 可重放性

```text
State + Event → State' + Output
```

只要 Initial State + [E1, E2, E3] 仍然存在,就可以重新计算得到 S3。
如果 Timer、线程、网络、真实时间都进入内核,这个性质会迅速变得困难。

## 15. 名称建议

当前 `World` 这个名字暗示"一个正在持续存在、自行运行的世界"。
如果它实际上是"一个拥有状态并接受事件、进行因果传播的图运行实例",
那么 `GraphInstance` / `GraphRuntime` / `GraphMachine` 可能更准确。
