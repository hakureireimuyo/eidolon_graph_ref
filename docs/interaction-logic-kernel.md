# Interaction Logic Kernel（应用层验证）

> 状态:2026-09-08 架构裁定,待实现
>
> 定位:本文档基于开放文字游戏推演,验证 Eidolon Kernel 形式模型在应用层的
> 边界表现。形式语义见 [kernel-formal-model.md](./kernel-formal-model.md)。
>
> 来源:2026-09-08 架构讨论(6 轮对话 + 开放世界推演)
>
> 相关:[kernel-formal-model.md](./kernel-formal-model.md)、
> [world-runtime-semantics.md](./world-runtime-semantics.md)、
> [graph-boundary-protocol.md](./graph-boundary-protocol.md)。

## 1. 核心定位

> **Eidolon Graph Kernel 不是世界运行时,而是一种事件驱动的交互逻辑能力:
> 它将宿主系统提供的世界事实组织成可编排的交互状态,并将交互结果以显式事件
> 返回宿主系统,从而为同一个世界增加一种新的交互与渲染模式。**

更精确地说:

> **Eidolon 的目的,是把传统程序中难以结构化表达的能力,转化为可编排、可组合、
> 可运行的程序结构。**

记忆、情绪、角色、语言交互、上下文推理、感知、叙事、Agent,不是 Eidolon
预先规定要支持的功能。它们只是这个表达系统具有足够表达能力之后,自然可以
构造出来的东西。

## 2. 非欧空间类比

传统游戏引擎负责:

```text
坐标系 → 物理 → 实体 → 资源 → 渲染 → 游戏运行时
```

不需要从底层推导一个非欧游戏引擎。只需要在传统引擎中划出一个区域:

```text
传统世界
──────────────────────────────
        │
        │ 普通区域
        │
        ├───────────────┐
        │               │
        │   Eidolon     │
        │   特殊交互域   │
        │               │
        │  "这里的空间   │
        │   按另一套规则 │
        │   被理解"      │
        │               │
        └───────────────┘
```

**世界本身仍然由传统引擎定义。**进入这个局部区域之后,某些东西不再按照
传统交互方式被表达,而是经过 Eidolon 的图,被重新组织成另一种交互体验。

## 3. 宿主程序与 Kernel 的分工

```text
             宿主程序
        ┌─────────────────┐
        │ World Truth     │
        │ Domain Truth    │
        │ Execution       │
        │ Persistence     │
        └────────┬────────┘
                 │
            observations
                 │
                 ▼
        ┌─────────────────┐
        │ Eidolon Graph   │
        │                 │
        │ Context         │
        │ Interpretation  │
        │ Orchestration   │
        │ Interaction     │
        │ Reasoning       │
        └────────┬────────┘
                 │
              commands
                 │
                 ▼
             宿主程序
```

**程序是真相来源,Graph 是交互解释器。**

### 宿主程序拥有世界事实

```text
玩家在哪里？玩家有多少钱？NPC 是否死亡？
门是否打开？现在几点？天气是什么？
这个物品是否存在？任务是否完成？
```

这些问题的最终答案来自宿主程序。Graph 不应该自己推导出一个"可能正确"的世界。

### Graph 拥有交互解释

```text
玩家这句话是什么意思？当前上下文应该如何组织？
应该向玩家展示什么？是否需要询问 LLM？
应该向世界请求什么动作？
这个世界结果应该如何进入当前叙事？
```

## 4. 开放输入,封闭结果空间

> **玩家可以提出任意语言形式的意图,但只有宿主程序能够决定这个意图
> 在世界中实际产生什么结果。**

```text
输入空间：开放
    ↓
语义空间：开放
    ↓
世界操作空间：由程序定义
    ↓
结果空间：由世界规则定义
```

玩家拥有的是:**选择权。**
程序拥有的是:**因果权。**

### LLM 的位置

**LLM 位于"语言 → 意图"和"事实 → 语言"两个边界上,而不是位于
"规则 → 结果"这个边界上。**

```text
玩家自然语言
    ↓
LLM 意图翻译
    ↓
结构化意图/请求
    ↓
Graph (上下文/逻辑/编排)
    ↓
World Command
    ↓
World (验证/执行)
    ↓
World Result
    ↓
Graph (组织)
    ↓
LLM 语言表达
    ↓
玩家
```

## 5. Kernel 不应该从功能定义

如果从功能出发:

```text
Eidolon Kernel
├── Memory        ← 不是 Kernel 特性
├── Emotion       ← 不是 Kernel 特性
├── Character     ← 不是 Kernel 特性
├── Dialogue      ← 不是 Kernel 特性
├── Agent         ← 不是 Kernel 特性
└── ...
```

正确的结构:

```text
Eidolon Kernel (基础表达语义)
├── Event
├── State
├── Node
├── Graph
├── Routing
├── Execution
├── Context
└── Boundary

上层应用 (在表达能力之上构造)
├── Memory = Node + State + Storage/Capability
├── Emotion = Graph-defined state transition
├── Character = Graph + external world entity
├── Text Adventure = Host World + Graph + LLM + Text Renderer
└── Agent = Perception + Context + Reasoning + Action Graph
```

**Kernel 的价值不来自"我内置了多少东西",而来自"我提供的最小语义能够
表达多少东西"。**

## 6. 节点为什么看起来什么都能做

节点不是"功能插件"。节点是:

> **一种可以被 Graph 编排的程序行为单元。**

只要一个能力可以被合理地表示为:

```text
输入状态/事件 → Node → 状态变化/事件输出
```

它就可以进入 Graph。Kernel 不需要知道"Memory 是什么",它只需要知道:
这是一个 Node,接受这些输入,产生这些输出,按 Graph 的事件语义运行。

## 7. 虚拟角色作为能力涌现的例子

### 世界实体 vs 交互主体

```text
                Virtual Character
                       │
          ┌────────────┴────────────┐
          │                         │
   World Representation       Interaction Mind
          │                         │
   External Game System       Eidolon Graph
          │                         │
   ├── position               ├── memory
   ├── body                   ├── emotion
   ├── inventory              ├── personality
   ├── health                 ├── context
   ├── skills                 ├── attention
   ├── relationships          ├── interpretation
   └── world state            ├── dialogue
                              ├── planning
                              └── response
```

外部程序负责这个角色作为世界实体"存在";Graph 负责这个角色作为
一个"交互主体"如何运作。

### 记忆

```text
玩家第一次见到角色
    ↓
"我叫艾琳。"
    ↓
Memory Graph
    ↓
保存：player.name = 艾琳知道的名字
```

几天以后:

```text
player.command → memory retrieval → context → LLM
→ "你又来了,之前你说过要去北边的矿场。"
```

"记得玩家"不是 LLM 自己记起来,而是 Graph 把记忆作为明确的状态
和能力组织起来。

### 情绪

```text
world.observation → "玩家杀死了角色的同伴"
    ↓
emotion graph → anger += 0.7, trust -= 0.8
    ↓
影响之后所有交互
```

### 外部感知

外部世界向角色提供 `world.observation`:

```text
玩家进入房间 / 敌人出现 / 天气变化 / 有人呼喊角色名字
```

Graph 决定:

```text
哪些值得注意 / 哪些进入短期上下文 / 哪些形成记忆
哪些改变情绪 / 哪些触发行为
```

外部世界负责"发生了什么";Graph 负责"这个角色如何感知、理解、
记住并回应这些事情"。

## 8. 能力涌现,而非功能预设

单独一个节点:

```text
事件 → 状态变化
```

几个节点组合:

```text
Observation → Memory → Context → LLM → Intent
```

已经产生了"记忆型交互"。加入 Emotion / Relationship / Attention / Planning
就形成了一个角色。连接 World Observation → Character Graph → World Command
就形成了一个可以存在于真实世界中的虚拟角色。

**但 Kernel 从头到尾没有增加一个"虚拟角色系统"。**这就是"表达能力"
与"功能列表"的根本区别。

## 9. 最终架构原则

```text
节点是表达能力
Graph 是组合能力
Event 是运行语义
Host 是现实世界
```

> **Eidolon 不负责实现某一种智能或交互能力;
> 它负责提供一种能够表达这些能力的程序结构。**

如果 Kernel 已经能够自然表达这些东西,那么继续增加 Kernel 功能
反而是在削弱它真正的价值。

## 10. 完整交互闭环

```text
                    HOST PROGRAM
          ┌───────────────────────────┐
          │                           │
          │   世界 / 业务 / 游戏 /     │
          │   数据 / 服务 / 生命周期   │
          │                           │
          │       Truth & Execution   │
          │                           │
          └─────────────┬─────────────┘
                        │
                 explicit Events
                        │
                        ▼
          ┌───────────────────────────┐
          │      EIDOLON KERNEL       │
          │                           │
          │   Event-driven Graph      │
          │                           │
          │   Nodes                   │
          │   Context                 │
          │   State                   │
          │   Routing                 │
          │   Reasoning               │
          │   Orchestration           │
          │                           │
          │   Special Interaction     │
          │   Semantics               │
          └─────────────┬─────────────┘
                        │
                 explicit Events
                        │
                        ▼
          ┌───────────────────────────┐
          │      INTERACTION          │
          │                           │
          │ Text / Voice / Agent / UI │
          │ Narrative / NPC / etc.    │
          └───────────────────────────┘
```

Eidolon 不定义世界,它定义一种与世界交互的方式。
