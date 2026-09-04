"""端口运行时状态：统一 PortState = 不变式（构建期）+ 运行事实 + 投递记录。

依据：graph-port-capability-composition.md §3.2 + REFACTOR_PORT_STATE_UNIFICATION.md
- Event 是事实，State 是事实的当前结果，pending 是状态变化尚未被当前执行消费的标记
- 三套冗余状态机（Data/Signal/Trigger）统一为单一 PortState：
  - PortInvariants（frozen）：port_type / is_wired / cache_strategy——构建期
    一次决定、构造时校验、运行时只读（不变式强化）
  - RuntimeFacts：value / level / pending——三种端口共享的可变事实
    （data: value=载荷或累积列表；signal: level；trigger: value=Data 载荷）
  - pending_deliveries：尚未消费的投递记录（直接引用，事件索引优化后）
- 粘性锁存（字段而非推导）：event_driven / has_value 只升不降——pending_deliveries
  消费即清空，动态资格与值资格必须跨 epoch 保持，无法从 pending 推导
  （REFACTOR 草案"len(pending_deliveries)>0"的写法有误）；has_payload 随消费复位

LOW 不拒数据、不清缓存：禁用的是参与执行的资格，不是接收能力。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ..model.ports import APPEND
from .event import Delivery, Event, Kind


@dataclass(frozen=True)
class PortInvariants:
    """构建期一次决定、运行时只读的端口不变式。"""

    port_type: Literal["data", "signal", "trigger"]
    is_wired: bool  # 由 in_index 决定；决定初始 event_driven
    cache_strategy: Literal["replace", "append"] | None = None  # 仅 data

    def __post_init__(self) -> None:
        if self.port_type == "data" and self.cache_strategy is None:
            raise ValueError("data port must specify cache_strategy")
        if self.port_type != "data" and self.cache_strategy is not None:
            raise ValueError(f"{self.port_type} port cannot have cache_strategy")


_INVARIANTS: dict[PortInvariants, PortInvariants] = {}


def shared_invariants(
    port_type: Literal["data", "signal", "trigger"],
    is_wired: bool,
    cache_strategy: Literal["replace", "append"] | None = None,
) -> PortInvariants:
    """不变式共享工厂：值对象按值去重——同型端口共享同一不变式对象。

    内存验收点（REFACTOR_PORT_STATE_UNIFICATION）：signal/trigger 端口全图
    各仅一个不变式对象；data 端口按 (is_wired, cache_strategy) 组合去重。
    构造校验与直连构造一致（key 构造即验证）。
    """
    key = PortInvariants(port_type, is_wired, cache_strategy)
    return _INVARIANTS.setdefault(key, key)


@dataclass
class RuntimeFacts:
    """三种端口共享的运行时可变事实。"""

    value: Any = None  # data: 载荷/累积列表；trigger: Data Event 载荷
    level: bool | None = None  # signal: HIGH/LOW（None=动态初始「?」，非 HIGH 非 LOW）
    pending: bool = False  # 状态变化尚未被当前执行消费


@dataclass
class PortState:
    """统一端口状态 = 不变式 + 运行事实 + 投递记录。

    - event_driven（仅 data）：参与触发——已连接数据线，或曾收到注入
      （图的入口点）。粘性锁存：初始 = is_wired，receive 置 True 后不复位。
    - has_value（仅 data）：有可用值——静态模式初始 True（默认属性），
      动态模式首次 receive 置 True；None 载荷合法，不可由 value 推导。
    - has_payload（仅 trigger）：自上次消费以来收到过 Data Event 载荷，
      consume_group 消费后复位。
    """

    invariants: PortInvariants
    facts: RuntimeFacts = field(default_factory=RuntimeFacts)
    pending_deliveries: list[Delivery] = field(default_factory=list)
    event_driven: bool = False
    has_value: bool = False
    has_payload: bool = False

    # 不变式便捷访问
    @property
    def port_type(self) -> str:
        return self.invariants.port_type

    @property
    def is_wired(self) -> bool:
        return self.invariants.is_wired

    @property
    def cache_strategy(self) -> str | None:
        return self.invariants.cache_strategy

    def receive(self, event: Event, delivery: Delivery) -> None:
        """Event 到达：照常进入、照常缓存（与资格无关），按 port_type 多态分派。"""
        self.event_driven = True
        if self.port_type == "data":
            self._receive_data(event)
        elif self.port_type == "signal":
            self.facts.level = bool(event.payload)
        else:  # trigger：激活请求；Data Event 时载荷可用（载荷 + 激活）
            if event.kind is Kind.DATA:
                self.facts.value = event.payload
                self.has_payload = True
        self.facts.pending = True
        self.pending_deliveries.append(delivery)

    def _receive_data(self, event: Event) -> None:
        """Data 缓存：Replace 覆盖 / Append 累积（缓存策略是端口声明属性）。

        Append 端口以列表累积；静态默认值若非列表，作为累积起点规范化。
        """
        if self.cache_strategy == APPEND:
            if not self.has_value:
                self.facts.value = [event.payload]
                self.has_value = True
            elif not isinstance(self.facts.value, list):
                self.facts.value = [self.facts.value, event.payload]
            else:
                self.facts.value.append(event.payload)
        else:
            self.facts.value = event.payload
            self.has_value = True
