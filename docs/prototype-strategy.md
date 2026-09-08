# Prototype Strategy（原型验证策略）

> 状态:2026-09-08 阶段裁定
>
> 定位:本文档记录 Eidolon 当前阶段的核心目标——验证计算模型,而非构建
> 完整生态系统。
>
> 相关:[kernel-formal-model.md](./kernel-formal-model.md)、
> [interaction-logic-kernel.md](./interaction-logic-kernel.md)。

## 1. 阶段重新定义

之前默认的前提:Eidolon 最终必须成为一套完整的、可生产使用的生态系统。

现在修正为:

> **验证"Graph Kernel + Event Semantics + Capability Composition"这套计算模型
> 本身是否成立。**

完成的定义:

$$
\boxed{
Kernel\ Prototype\ Complete
\iff
Core\ Semantics\ Have\ Survived\ Representative\ Cases
}
$$

不是代码量达到多少,也不是 API 齐全,而是**它已经经受住了足够不同的
计算模型的压力**。

## 2. 语言策略

$$
\boxed{Python = Reference\ Implementation}
$$

$$
\boxed{Rust = Native\ Implementation}
$$

Python 不是 Eidolon 的最终实现语言,而是 Eidolon 的第一代实验语言。
Rust 也不是重新发明 Eidolon,而是 Eidolon 语义的第二个实现。

### Godot 集成路线

```text
Godot
├── GDScript → 游戏表现 / 场景胶水
├── Rust → Eidolon Kernel + Capability Runtime (via GDExtension)
└── Godot Engine → Rendering / Physics / Audio / Scene
```

Rust 不需要语言 VM,最终产物:

```text
game.exe
├── Godot
├── Eidolon native library
└── game resources
```

### 语言排序

| 语言 | Godot 接入 | VM/Runtime | 定位 |
|------|-----------|------------|------|
| **Rust** | GDExtension | 不需要 | **Native 首选** |
| C++ | GDExtension | 不需要 | 最稳妥 |
| C# | 官方 .NET | 需要 .NET runtime | 可以但不是首选 |
| GDScript | 原生 | Godot 自带 | 做 Host 不做 Kernel |
| **Python** | 非原生 | 需要 Python VM | **第一代参考实现** |

### 双层实现

```text
             Eidolon Semantics
                    │
          ┌─────────┴─────────┐
          ↓                   ↓
   Python Reference       Rust Native
          │                   │
          ↓                   ↓
     Python Host           Godot
```

两者遵循同一套形式语义:

$$
K_{Python}(S,e) \equiv K_{Rust}(S,e)
$$

Python 版本作为 native 实现的行为基准(reference implementation)。

## 3. 两层工程分离

```text
                 Eidolon
                    │
        ┌───────────┴───────────┐
        │                       │
   Semantic Core          Experimental Product
        │                       │
   要求严格                 允许粗糙
   要求一致                 可以硬编码
   要求可解释               可以临时实现
   要求可测试               不要求通用
   关注语义                 关注结果
```

**Kernel 是科学实验对象。**需要严谨,因为是在判断一个新的计算模型是否成立。

**Product 是实验仪器。**不需要优雅。越不追求架构完整,有时候越好,
因为可以快速暴露 Kernel 本身的问题。

## 4. 实验产品策略

> **内核负责验证计算模型,产品负责验证模型有没有实际价值。**

产品是一次性、目标明确、允许大量硬编码的实验性产品。

```text
一个具体想法
      ↓
用 Eidolon Kernel 建一个 Graph
      ↓
补几个最简单的 Capability
      ↓
做成一个能实际运行的东西
      ↓
真正使用它
      ↓
观察哪里不自然
```

### 候选实验方向

| 实验 | 测试的语义 |
|------|-----------|
| 有记忆的 Agent | State + Event + Capability + 异步 + Memory + Boundary |
| 具有持续状态的虚拟角色 | 长生命周期状态 + 外部输入 + 能力调用 |
| 复杂工作流 | fan-out/fan-in + failure + retry + ordering |
| 交互式叙事 | 上下文推理 + LLM 编排 + 多阶段交互 |

### 产品可以故意"作弊"

```python
character_memory = {}
character_personality = {...}
special_case = ...
```

甚至某个功能直接调用一个 Python 函数。只要能帮助回答:

> "这个 Graph 模型是否真的能承载我要表达的行为？"

就是有效实验。

## 5. 边界纪律

**产品可以不严谨,Kernel 的语义不能因为产品赶进度而被污染。**

闭环:

```text
产品需求
   ↓
发现表达困难
   ↓
判断是不是一般性语义问题
   ↓
       ┌───────────────┐
       │               │
      否              是
       │               │
   产品临时解决       修正 Kernel
                       ↓
                  更新语义测试
```

## 6. 当前阶段应该保留的东西

```text
                ┌──────────────────────┐
                │      Prototype       │
                │                      │
                │   Capability Cases   │
                └──────────┬───────────┘
                           │
                ┌──────────▼───────────┐
                │      Graph Model     │
                │                      │
                │ Node / Port / Group  │
                │ Wire / Endpoint      │
                └──────────┬───────────┘
                           │
                ┌──────────▼───────────┐
                │    Event Kernel      │
                │                      │
                │ State / Event /      │
                │ Delivery / Timeline  │
                └──────────┬───────────┘
                           │
                ┌──────────▼───────────┐
                │   Semantic Tests     │
                └──────────────────────┘
```

## 7. 当前阶段不需要的东西

| 不需要 | 原因 |
|--------|------|
| 插件系统 | 还没证明"能力需要以插件形式分发" |
| 完整资源系统 | 最小 Resource 抽象即可 |
| 复杂 Host | `while True: dispatch(event)` 已足够 |
| 可视化编辑器 | DSL + Python 构造器即可 |
| 完整持久化 | 只需验证 Definition → Runtime → Trace → Replay |
| Godot 集成 | 现在完全不需要 |
| Rust Kernel | 现在不需要,语义稳定后再做 |

## 8. 核心原则

> **不要实现你认为未来需要的东西,只实现验证当前假设所需要的东西。**

未来的生态应该从实验产品中反向生长出来,而不是提前设计出来。

## 9. 真正需要积累的是

```text
Event Semantics
Graph Semantics
State Semantics
Boundary Semantics
Capability Semantics
+
Tests
+
Traces
+
Counterexamples
```

而不是一套必须永远维护的 Python 工程。

Python Kernel 甚至可以被允许最终废弃,只要它完成了自己的使命:
**证明这套计算语义值得存在。**

$$
\boxed{
Semantics \neq Implementation
}
$$
