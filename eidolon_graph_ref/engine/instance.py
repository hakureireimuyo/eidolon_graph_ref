"""GraphInstance：图定义 + 运行态（世界 = 状态机/程序：注入 → 传播至静止）。

依据：graph-execution-model.md §1/§4/§5
- 规则（图定义）与事实（节点状态/端口状态）分离；运行期拓扑不可变
- 节点状态是单写者：执行时读取（深拷贝），产出新状态直接提交
- 静态/动态 = 同一端口的两种运行模式：连接状态决定输入语义，
  由内核吸收进 Readiness 计算（节点实现不感知）
- 静态值 = 配置覆盖(按端口名)或端口声明默认；动态初始 =「尚未收到事件」
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..model.graph import GraphDefinition, SLOT_DATA, SLOT_QUAL, SLOT_SIGNAL, SLOT_TRIGGER, Wire
from ..model.node_type import NodeType
from ..model.ports import APPEND
from .event import Injection
from .executor import Executor
from .port_state import DataPortState, SignalPortState, TriggerPortState
from .timeline import Timeline


class GraphInstance:
    """一张图的运行实例：事实（节点状态 + 端口状态 + 时间线/事件档案）。"""

    def __init__(self, definition: GraphDefinition, types: dict[str, NodeType]):
        self.definition = definition
        self.types = types
        self.run_no = 0  # 已完成的 epoch 数
        self.timeline = Timeline()
        self.log: list[str] = []  # 错误日志（独立于时间线：程序打印了什么）

        # 节点事实
        self.node_states: dict[str, dict[str, Any]] = {}
        self.configs: dict[str, dict[str, Any]] = {}
        # 端口状态（按 (节点, 端口名) 索引；只有"已连接"的动态端口才创建 Signal 状态）
        self.data_states: dict[str, dict[str, DataPortState]] = {}
        self.qual_states: dict[str, dict[str, SignalPortState]] = {}  # 资格槽
        self.trigger_states: dict[str, dict[str, TriggerPortState]] = {}
        self.enable_states: dict[str, dict[str, SignalPortState]] = {}  # enable(节点级资格)

        # 连线索引
        self.in_index: dict[tuple[str, str, str], Wire] = {}
        self.out_index: dict[tuple[str, str], list[Wire]] = {}

        self._build()

    # ================================================================== 构建
    def _build(self) -> None:
        from ..model.validate import resolve_slots

        resolve_slots(self.definition, self.types)  # 连线槽位按端口声明自动推断
        for wire in self.definition.wires:
            self.in_index[(wire.dst_node, wire.dst_port, wire.dst_slot)] = wire
            self.out_index.setdefault((wire.src_node, wire.src_port), []).append(wire)

        for nid, spec in self.definition.nodes.items():
            ntype = self.types[spec.type]
            self.configs[nid] = {**ntype.config_defaults, **spec.config}
            self.node_states[nid] = deepcopy(ntype.state_defaults)

            data_states: dict[str, DataPortState] = {}
            qual_states: dict[str, SignalPortState] = {}
            for p in ntype.data_in:
                wired = (nid, p.name, SLOT_DATA) in self.in_index
                # 静态模式：配置覆盖(按端口名)或声明默认；动态模式：尚未收到事件
                static_value = self.configs[nid].get(p.name, p.default)
                if p.cache == APPEND and static_value is None:
                    static_value = []  # Append 端口的静态空态是空列表，而非 None
                data_states[p.name] = DataPortState(
                    cache=p.cache,
                    value=static_value,
                    has_value=not wired,
                    event_driven=wired,
                )
                if (nid, p.name, SLOT_QUAL) in self.in_index:
                    qual_states[p.name] = SignalPortState()
            self.data_states[nid] = data_states
            self.qual_states[nid] = qual_states
            self.trigger_states[nid] = {t.name: TriggerPortState() for t in ntype.trigger_in}
            self.enable_states[nid] = {
                s.name: SignalPortState() for s in ntype.signal_in if (nid, s.name, SLOT_SIGNAL) in self.in_index
            }

    # ================================================================== 运行
    def run(self, injections: list[Injection] | None = None) -> None:
        """推进一个 epoch（注入 → 传播至静止）。"""
        Executor(self.types).run(self, injections or [])

    # ================================================================== 观察
    def observable_state(self) -> dict:
        """全量可观察状态：节点 state 字段 + 端口状态（编辑器/控制台/测试）。"""
        result: dict[str, Any] = {}
        for nid in self.definition.node_order():
            spec = self.definition.nodes[nid]
            node_view: dict[str, Any] = {
                "type": spec.type,
                "state": deepcopy(self.node_states[nid]),
                "config": dict(self.configs[nid]),
                "data_in": {
                    name: {
                        "value": st.value,
                        "has_value": st.has_value,
                        "pending": st.pending,
                        "qual": (
                            {"level": qs.level, "pending": qs.pending}
                            if (qs := self.qual_states[nid].get(name)) is not None
                            else None
                        ),
                    }
                    for name, st in self.data_states[nid].items()
                },
                "trigger_in": {
                    name: {"pending": st.pending, "payload": st.payload if st.has_payload else None}
                    for name, st in self.trigger_states[nid].items()
                },
                "enable": {
                    name: {"level": st.level, "pending": st.pending}
                    for name, st in self.enable_states[nid].items()
                },
            }
            result[nid] = node_view
        return result
