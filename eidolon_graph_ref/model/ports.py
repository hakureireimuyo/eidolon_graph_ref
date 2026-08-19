"""端口声明模型。

依据：graph-ports-bindings.md §2.1 / graph-port-capability-composition.md §3.2-3.3

- **一个端口一种声明**：不存在独立的"静态端口/动态端口"类型。
  静态(未连接)/动态(已连接)是同一端口的两种运行模式，由连接状态决定，
  由内核吸收进 Readiness 计算，节点实现不感知。
- DataIn：一格缓存(Replace 默认 / Append 声明) + pending；可声明资格槽(qualified)。
- TriggerIn：函数调用入口(激活请求)。Data Event = 载荷 + 激活；Signal Event = 激活。
- SignalIn：节点级资格 enable（level + pending）。
- SignalOut：仅信号节点声明。数据节点(不声明 SignalOut)永不触碰信号。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 数据端口的缓存策略（graph-port-capability-composition.md §3.2：缓存策略是端口属性）
REPLACE = "replace"  # 一格覆盖：新值覆盖旧值（默认）
APPEND = "append"  # 累积：新值追加（Buffer 类）


@dataclass(frozen=True)
class DataIn:
    """数据输入端口声明。

    - default：静态模式的默认属性（也作为动态模式未收到事件时参与计算的兜底值）
    - cache：REPLACE / APPEND，缓存策略是端口属性
    - qualified：是否声明资格槽（可选）。已连接资格槽的端口 Readiness 需
      pending AND level == HIGH；未连接 = 条件恒成立（结构属性，非隐式事件）
    """

    name: str
    default: Any = None
    cache: str = REPLACE
    qualified: bool = False


@dataclass(frozen=True)
class DataOut:
    """数据输出端口声明。执行时写入即投递(Data Event)；不写即不投递。"""

    name: str


@dataclass(frozen=True)
class TriggerIn:
    """触发端口声明 = 函数调用入口(激活请求)。

    Data Event 到达 = 载荷 + 激活；Signal Event 到达 = 激活(每次事件都是新的
    激活请求，同电平重复也有效)。载荷经 ctx.data_in[端口名] 可用。
    """

    name: str


@dataclass(frozen=True)
class SignalIn:
    """信号输入端口声明 = 节点级资格(enable)。

    与端口级资格槽是同一机制的两种作用域：level + pending。
    enable 是持续电平门控（章节门控语义）：Readiness 只看 level == HIGH；
    pending 仅触发重估后消费。LOW = 整节点不执行，数据照常接收缓存。
    """

    name: str


@dataclass(frozen=True)
class SignalOut:
    """信号输出端口声明。仅信号节点声明：读输入信号、写输出信号。

    电平输出 = Signal Event；未写保持原电平。
    """

    name: str


def data_in_names(ports: list[DataIn]) -> list[str]:
    return [p.name for p in ports]
