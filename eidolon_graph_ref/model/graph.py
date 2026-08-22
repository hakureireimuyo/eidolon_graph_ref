"""图定义：节点实例 + 连线（Python API 构建，运行期拓扑不可变）。

依据：graph-runtime-overview.md §2 / node-protocol.md §2
- 图 = 节点实例 + 连线：用户编辑的产物，世界运行蓝图
- 运行时不允许编辑：GraphDefinition 创建后拓扑不可变
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .assets import AssetRef

# 连线目标槽位：决定"这条线传递什么、端口如何消费它"
# DataIn/TriggerIn/SignalIn each have one receiving slot.
SLOT_DATA = "data"
SLOT_TRIGGER = "trigger"
SLOT_SIGNAL = "signal"


@dataclass(frozen=True)
class Wire:
    """一条连线 = 一次事件投递的静态路径。

    源 = 输出端口(DataOut/SignalOut)；目标 = 输入端口 + 槽位。
    dst_slot 为 None 时由校验/实例构建按端口声明自动推断
    （TriggerIn→trigger、SignalIn→signal、DataIn→data；
    SignalOut→已声明资格槽的 DataIn 二义，必须显式指定）。
    扇入禁止：每个(节点, 端口, 槽位)至多一条线；扇出无限。
    """

    src_node: str
    src_port: str
    dst_node: str
    dst_port: str
    dst_slot: str | None = None


@dataclass(frozen=True)
class NodeSpec:
    """节点实例 = 类型 + 配置覆盖。状态是运行态，不属于图定义。"""

    id: str
    type: str
    config: dict[str, Any] = field(default_factory=dict)


class GraphDefinition:
    """图定义（规则），与运行实例（事实）分离。"""

    def __init__(self, name: str = "graph"):
        self.name = name
        self._nodes: dict[str, NodeSpec] = {}
        self._wires: list[Wire] = []
        self._asset_bindings: dict[tuple[str, str], AssetRef] = {}

    # ---- 构建 API ----------------------------------------------------------
    def add_node(self, node_id: str, type_name: str, **config: Any) -> GraphDefinition:
        if node_id in self._nodes:
            raise ValueError(f"duplicate node id {node_id!r}")
        # Config is deliberately partitioned by lifecycle.  Keep a single
        # ``config={...}`` argument ergonomic while rejecting flat overrides.
        if set(config) == {"config"}:
            config = dict(config["config"])
        self._nodes[node_id] = NodeSpec(id=node_id, type=type_name, config=dict(config))
        return self

    def wire(
        self,
        src_node: str,
        src_port: str,
        dst_node: str,
        dst_port: str,
        slot: str | None = None,
    ) -> GraphDefinition:
        self._wires.append(
            Wire(src_node=src_node, src_port=src_port, dst_node=dst_node, dst_port=dst_port, dst_slot=slot)
        )
        return self

    def add_wire(self, wire: Wire) -> GraphDefinition:
        self._wires.append(wire)
        return self

    def bind_asset(self, node_id: str, slot: str, asset_id: str) -> GraphDefinition:
        """绑定 (节点, 槽位) → AssetRef(编辑期纯数据,§7 裁定:绑定归属图定义)。

        共享/独立不由 Runtime 强制,完全由指向哪个 asset_id 决定(§7 裁定);
        绑定唯一由键 (node, slot) 保证,重复绑定即报错。
        """
        key = (node_id, slot)
        if key in self._asset_bindings:
            raise ValueError(f"duplicate asset binding {key!r}")
        self._asset_bindings[key] = AssetRef(asset_id)
        return self

    # ---- 查询 ---------------------------------------------------------------
    @property
    def nodes(self) -> dict[str, NodeSpec]:
        return dict(self._nodes)

    @property
    def wires(self) -> tuple[Wire, ...]:
        return tuple(self._wires)

    @property
    def asset_bindings(self) -> dict[tuple[str, str], AssetRef]:
        """资产绑定表:键 (节点, 槽位) → AssetRef。"""
        return dict(self._asset_bindings)

    def node_order(self) -> list[str]:
        """节点声明序（播种等使用）。"""
        return list(self._nodes.keys())
