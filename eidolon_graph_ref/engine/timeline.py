"""时间线 + 事件档案：Kernel Trace 的底层。

依据：graph-execution-model.md §4（因果 trace：run+seq 确定性时间线，
记录世界为什么变成这个状态）+ 用户裁定（事件档案 = 传播分析/追踪/未来可视化的基础）

- 时间线：按序的传播事实（inject / deliver / fire / consume / error / quiesce）
- 事件档案：所有产生过的事件（含生命周期），被消费后暂时保留
- 独立于错误日志（实例 log），不进状态（验证阶段无持久化）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .event import Event

KIND_DELIVER = "deliver"  # 事件投递（宿主注入与节点产出同构；src=None = 宿主）
KIND_FIRE = "fire"  # 组执行（消费哪些事件、产出哪些事件）
KIND_CONSUME = "consume"  # 无执行的 pending 消费（enable 通知 / 资格槽 LOW 自我消费）
KIND_ERROR = "error"  # tick 异常或声明违规
KIND_QUIESCE = "quiesce"  # 队列排空，epoch 静止


@dataclass
class Entry:
    """统一时间线条目（按 kind 使用不同字段）。"""

    run: int
    seq: int = 0  # 由 Timeline.record 分配
    kind: str = ""
    event_id: int | None = None
    payload: Any = None
    src_node: str | None = None
    src_port: str | None = None
    dst_node: str | None = None
    dst_port: str | None = None
    dst_slot: str | None = None
    group: str | None = None
    consumed: tuple[int, ...] = ()
    produced: tuple[int, ...] = ()
    message: str | None = None


class Timeline:
    """run+seq 确定性时间线 + 事件档案。"""

    def __init__(self) -> None:
        self.entries: list[Entry] = []
        self.events: dict[int, Event] = {}
        self.next_event_id = 1
        self.next_seq = 1

    def new_event_id(self) -> int:
        eid = self.next_event_id
        self.next_event_id += 1
        return eid

    def record(self, entry: Entry) -> Entry:
        entry.seq = self.next_seq
        self.next_seq += 1
        self.entries.append(entry)
        return entry

    def archive(self, event: Event) -> None:
        self.events[event.id] = event

    def epoch_entries(self, run: int) -> list[Entry]:
        return [e for e in self.entries if e.run == run]
