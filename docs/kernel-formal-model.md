# Kernel Formal Model（内核形式语义）

> 状态:2026-09-08 架构裁定,待实现
>
> 定位:本文档建立 Eidolon Kernel 的数学语义。
>
> **Eidolon Kernel 的真正定位:一个能够把复杂逻辑结构化、可视化,并以事件驱动
> 方式进行状态转换的可执行图模型。**
>
> 数学语义用于检验架构裁定是否自洽,并反过来约束实现。
>
> 来源:2026-09-08 架构讨论 + 开放世界推演
>
> 相关:[graph-boundary-protocol.md](./graph-boundary-protocol.md)、
> [world-runtime-semantics.md](./world-runtime-semantics.md)、
> [interaction-logic-kernel.md](./interaction-logic-kernel.md)。

## 1. 核心函数

$$
\boxed{
\mathcal{K} : (S,E)\rightarrow(S',O)
}
$$

- $S$ : Graph Kernel 在某一时刻的内部状态
- $E$ : 一个进入 Kernel 的事件
- $S'$ : 事件传播完成后的新状态
- $O$ : 这一事件产生的 Graph Output

## 2. Graph 定义

编译完成的 Graph:

$$
G=(N,P,W,I,O,A)
$$

- $N=\{n_1,n_2,\dots,n_k\}$ : 节点集合
- $P(n)=P_{in}(n)\cup P_{out}(n)$ : 每个节点的端口集合
- $W\subseteq P_{out}\times P_{in}$ : Wire 静态关系
- $I$ : InputEndpoint 集合
- $O$ : OutputEndpoint 集合
- $A$ : AssetBinding 集合

Wire 是一个有向多重图。$(p_a,p_b)\in W$ 表示事件从 $p_a$ 产生后可以传播到 $p_b$。

对应实现:

```text
out_index[(node, port)]
        ↓
Wire
        ↓
(node, port, slot)
```

## 3. Endpoint 映射

输入端点:

$$
\tau_{in}:I\rightarrow P_{in}
$$

例如:

$$
\tau_{in}(\text{user\_message})
=
(\text{llm},\text{prompt},\text{DATA})
$$

输出端点(观察关系):

$$
\tau_{out}:P_{out}\rightarrow \mathcal{P}(O)
$$

例如:

$$
\tau_{out}(\text{llm.response})
=
\{\text{assistant\_message}\}
$$

使用集合是因为一个内部端口可以对应多个 OutputEndpoint。

## 4. Event

$$
e=(id,r,k,v,p,n)
$$

- $id\in\mathbb N$ : 事件身份
- $r\in\mathbb N$ : 运行 epoch
- $k\in\{\text{DATA},\text{SIGNAL}\}$ : Kind
- $v\in V$ : payload
- $p\in N\cup\{\bot\}$ : producer ($\bot$ = 宿主注入,对应 `producer=None`)
- $n\in P$ : 产生端口

核心不变量:

$$
\boxed{Endpoint\notin Event}
$$

Event 不携带外部语义。`producer/port` 是 Kernel 内部产生位置。

## 5. Injection 与 InputEvent 的区分

测试注入:

$$
J=(n,p,s,k,v)
$$

正式外部输入:

$$
X=(i,v),\quad i\in I
$$

Translation Layer:

$$
T_{in}:X\rightarrow J
$$

$$
T_{in}(i,v)
=
(\tau_{in}(i).node,\tau_{in}(i).port,
\tau_{in}(i).slot,k,v)
$$

完整路径:

$$
\boxed{
External
\xrightarrow{T_{in}}
Injection\text{-like target}
\xrightarrow{K}
Event
}
$$

`Injection` 是地址解析后的输入描述,不是 Kernel Event 模型的一部分。

## 6. Kernel State

$$
S=(Q,R,T)
$$

- $Q=\prod_{n\in N}State(n)$ : 所有节点端口当前状态
- $R$ : 运行时事件/投递记录
- $T$ : Timeline

节点状态:

$$
State(n)=(D_n,\Sigma_n,\Theta_n)
$$

- $D_n$ : data slot 状态
- $\Sigma_n$ : signal slot 状态
- $\Theta_n$ : trigger/pending 状态

对应实现: `PortState` + `pending_deliveries` + `RuntimeFacts`。

## 7. Group 转换

Group 是一个状态转换函数:

$$
g: State(n)\rightarrow (State(n)',E^*)
$$

Readiness:

$$
Ready(g,S)\in\{0,1\}
$$

如果 $Ready(g,S)=1$,则:

$$
g(S)=(S',E_1,\dots,E_k)
$$

对应实现: `group_ready` → `fire` → `emit`。

## 8. 事件传播与 Quiescence

外部输入产生 $e_0$,Kernel 执行:

$$
Deliver(S,e_0,p) \rightarrow S_1
$$

检查所有满足条件的 Group:

$$
S_1 \xrightarrow{g_1} S_2 \xrightarrow{g_2} S_3 \rightarrow \dots \rightarrow S_n
$$

最终:

$$
\forall g,\quad Ready(g,S_n)=0
$$

Quiescence 严格定义:

$$
\boxed{
Quiescent(S)
\iff
\forall g\in G,\; \neg Ready(g,S)
}
$$

这表示"当前事件造成的因果传播已经没有下一步",不是"世界停止"。

## 9. Output 定义

事件传播过程中产生事件集合 $E^*=\{e_1,e_2,\dots,e_k\}$。

Boundary 观察:

$$
Boundary(e)=
\{o\in O\mid source(o)=producer(e,port(e))\}
$$

最终输出:

$$
O_{run}
=
\bigcup_{e\in E^*}Boundary(e)
$$

因此:

$$
\boxed{
\mathcal K(S,e)
=
(S',O_{run})
}
$$

Output 是对 Event 的**观察结果**,不是 Event 的属性:

$$
Event \xrightarrow{observe} OutputRecord
$$

## 10. orphan 形式化定义

$$
\boxed{
orphan(e)\iff Deliveries(e)=\varnothing
}
$$

$$
\boxed{
pending(e)\iff \exists d\in Deliveries(e),\; consumed(d)=\bot
}
$$

$$
\boxed{
consumed(e)\iff Deliveries(e)\neq\varnothing \;\wedge\; \forall d\in Deliveries(e),\; consumed(d)\neq\bot
}
$$

Output 不出现在 orphan 定义中。因此:

$$
\boxed{
orphan(e)\not\Rightarrow\neg Output(e)
}
$$

$$
\boxed{
Output(e)\not\Rightarrow\neg orphan(e)
}
$$

一个 Output Event 可以同时是 orphan 和 Graph Output。

## 11. 完整系统:双状态机

外部系统 $\mathcal X$ 拥有自己的状态 $X_t$:

$$
\boxed{
\begin{aligned}
S_{t+1}&=K(S_t,E_t) &\text{(Kernel 状态转换)}\\
X_{t+1}&=F(X_t,O_t) &\text{(外部系统状态转换)}\\
E_{t+1}&=G(X_{t+1}) &\text{(外部系统产生输入)}\\
O_t&=B(S_t,S_{t+1}) &\text{(Boundary 观察)}
\end{aligned}
}
$$

两个状态机通过事件连接。Kernel 不需要拥有外部系统。

## 12. Resource 隐患的形式化

如果节点通过 DI 访问 Resource $R$:

$$
g(S,R)\rightarrow (S',E^*)
$$

如果外部系统在没有 Event 的情况下修改 $R_t\rightarrow R_{t+1}$:

$$
S'=K(S,E,R_t)
$$

而 $R_t$ 不在 Timeline 中。同一个 $(S,E)$ 可能得到不同结果:

$$
K(S,E,R_1)\neq K(S,E,R_2)
$$

这破坏了确定性:

$$
\boxed{
Same\ State + Same\ Event \Rightarrow Same\ Transition
}
$$

**资源并不天然是错误的;真正危险的是不可观察的外部状态参与 Kernel Transition。**

判断标准:这个东西是否会影响 Kernel 的状态转换?如果会,它的影响是否能够被事件模型完整表达和追踪?

## 13. 架构定理

Kernel 满足:

$$
\boxed{
S_{n+1}=K(S_n,E_n)
}
$$

所有影响状态转换的输入,必须归入 $E_n$ 或编译时固定的 $G$。
不能存在 Kernel 无法观察的动态变量 $X_n$,否则:

$$
S_{n+1}=K(S_n,E_n,X_n)
$$

而 $X_n$ 不在 Timeline 中。

## 14. 最终 Kernel 定义

$$
\boxed{
\mathcal K_G:
(S,E)
\rightarrow
(S',E^*,T)
}
$$

- $G$ : 静态结构(编译时确定)
- $S$ : 唯一运行时状态
- $E$ : 唯一动态输入载体
- $E^*$ : 传播产生的事件集合
- $T$ : 可审计的传播轨迹

Input/Output Endpoint 属于 Kernel 外部的 Boundary Protocol,只负责把外部世界
与这个数学对象连接起来。

## 15. 应用层验证:交互逻辑内核

上述数学模型在应用层的验证——开放文字游戏推演证明:

- 宿主程序拥有世界事实(Truth & Execution)
- Graph 拥有交互解释(Context & Interpretation)
- LLM 位于"语言→意图"和"事实→语言"边界,不在"规则→结果"边界
- 玩家拥有选择权,程序拥有因果权
- "开放输入,封闭结果空间"
- 记忆/情绪/角色/Agent 是表达能力的自然涌现,不是 Kernel 预设功能

详见 [interaction-logic-kernel.md](./interaction-logic-kernel.md)。
