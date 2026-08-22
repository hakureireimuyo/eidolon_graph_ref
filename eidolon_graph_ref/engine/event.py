"""事件：唯一传播事实，有身份、有生命周期、记录谁生产谁消费。

依据：graph-port-capability-composition.md §3.1 + 用户裁定（2026-08-19）
- Event 是图之间运行时交互层的唯一传递事实；Data/Signal 是载荷语义
- 事件有独立身份(id)；记录生产者(producer)与消费记录(consumed_by)
- 生命周期：produced → delivered(可多次,扇出) → consumed
- 被消费后的事件暂时保留在事件档案中，是传播分析/追踪(以及后续可视化)的底层基础
- 事件彼此独立，不引入同因果组绑定（causal_id 裁定取消）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..model.graph import SLOT_DATA, SLOT_SIGNAL, SLOT_TRIGGER


class Kind(str, Enum):
    DATA = "data"  # 数据事件（载荷 + 在 TriggerIn 处兼作激活）
    SIGNAL = "signal"  # 信号事件（电平；在 TriggerIn 处为纯激活）


@dataclass
class Delivery:
    """一次投递 = 一条线、一次下游端口状态更新。"""

    event_id: int
    node: str  # 目标节点
    port: str  # 目标输入端口
    slot: str  # 目标槽位（data/qual/trigger/signal）
    seq: int  # 时间线序号
    consumed_seq: int | None = None  # 该投递的 pending 被消费的时间线序号


@dataclass
class Event:
    """一个有身份的事件。

    - producer：产出节点 id；None = 宿主注入
    - port：产出端口名（宿主注入时为目标端口名）
    - deliveries：每次投递（扇出 = 多次投递，每次独立更新一个端口状态）
    - consumed_by：(seq, node, port) —— 每次被消费的记录
    """

    id: int
    run: int  # 产生于哪个 epoch
    kind: Kind
    payload: Any  # data=值；signal=电平(HIGH/LOW)
    producer: str | None
    port: str | None
    deliveries: list[Delivery] = field(default_factory=list)
    consumed_by: list[tuple[int, str, str]] = field(default_factory=list)  # (seq, node, port)

    @property
    def status(self) -> str:
        """生命周期状态（派生）。

        - orphan：产出后没有任何投递（输出端口未连线）
        - pending：存在尚未被消费的投递
        - consumed：全部投递的 pending 已被消费
        """
        if not self.deliveries:
            return "orphan"
        if all(d.consumed_seq is not None for d in self.deliveries):
            return "consumed"
        return "pending"


@dataclass(frozen=True)
class Injection:
    """宿主注入：一次外部事件（与节点产出同构，producer=None）。"""

    node: str  # 目标节点
    port: str  # 目标输入端口
    slot: str  # 目标槽位
    kind: Kind
    payload: Any = None

    def __post_init__(self) -> None:
        if self.slot == SLOT_DATA and self.kind is not Kind.DATA:
            raise ValueError("injection into data slot must be a data event")
        if self.slot == SLOT_SIGNAL and self.kind is not Kind.SIGNAL:
            raise ValueError(f"injection into {self.slot!r} slot must be a signal event")
        if self.slot == SLOT_TRIGGER and self.kind not in (Kind.DATA, Kind.SIGNAL):
            raise ValueError("injection into trigger slot must be a data or signal event")
