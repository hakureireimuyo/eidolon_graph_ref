"""端口运行时状态。

依据：graph-port-capability-composition.md §3.2
- Event 是事实，State 是事实的当前结果，pending 是状态变化尚未被当前执行消费的标记
- Data：value + pending（Replace 覆盖 / Append 累积——缓存策略是端口声明属性）
- Signal：level + pending（level 消费后保持；同电平重复 S1→S1 是两次独立资格）
- Trigger：pending（激活请求）；收到 Data Event 时载荷可用（载荷 + 激活）
- pending_events：尚未消费的事件 id —— 事件身份追踪（消费记录 = fire 的 consumed）

LOW 不拒数据、不清缓存：禁用的是参与执行的资格，不是接收能力。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..model.ports import APPEND, REPLACE
from .event import Event, Kind


@dataclass
class DataPortState:
    cache: str = REPLACE  # 声明属性：Replace / Append
    value: Any = None  # Replace: 单值；Append: 累积列表
    has_value: bool = False  # 静态模式=True(默认属性)；动态模式初始 False=「尚未收到事件」
    pending: bool = False
    pending_events: list[int] = field(default_factory=list)
    event_driven: bool = False  # 参与触发：已连接数据线，或曾收到注入（图的入口点）

    def receive(self, event: Event) -> None:
        """Data Event 到达：照常进入、照常缓存（与资格无关）。

        首次收到事件 = 端口进入动态模式（宿主注入目标与连线同为外部事件驱动）。
        Append 端口以列表累积；静态默认值若非列表，作为累积起点规范化。
        """
        self.event_driven = True
        if self.cache == APPEND:
            if not self.has_value:
                self.value = [event.payload]
                self.has_value = True
            elif not isinstance(self.value, list):
                self.value = [self.value, event.payload]
            else:
                self.value.append(event.payload)
        else:
            self.value = event.payload
            self.has_value = True
        self.pending = True
        self.pending_events.append(event.id)


@dataclass
class SignalPortState:
    level: bool | None = None  # None=动态初始「?」(非 HIGH 非 LOW)；HIGH/LOW
    pending: bool = False
    pending_events: list[int] = field(default_factory=list)

    def receive(self, event: Event) -> None:
        self.level = bool(event.payload)
        self.pending = True
        self.pending_events.append(event.id)


@dataclass
class TriggerPortState:
    pending: bool = False
    pending_events: list[int] = field(default_factory=list)
    payload: Any = None  # Data Event 的载荷（载荷 + 激活）
    has_payload: bool = False

    def receive(self, event: Event) -> None:
        if event.kind is Kind.DATA:
            self.payload = event.payload
            self.has_payload = True
        self.pending = True
        self.pending_events.append(event.id)
