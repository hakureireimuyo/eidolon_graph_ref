"""测试辅助：建图、运行、断言工具。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eidolon_graph_ref.engine.instance import GraphInstance
from eidolon_graph_ref.engine.timeline import KIND_DELIVER, KIND_ERROR, KIND_FIRE, KIND_QUIESCE
from eidolon_graph_ref.model.graph import GraphDefinition
from eidolon_graph_ref.model.node_type import NodeType
from eidolon_primitives import PRIMITIVES


def make_world(build, types=None) -> GraphInstance:
    """构建图定义并创建运行实例（唯一正式路径：GraphInstance.build）。"""
    g = build() if callable(build) else build
    result = GraphInstance.build(g, types or PRIMITIVES)
    assert result.ok, result.errors
    return result.instance


def fired(world: GraphInstance, run: int | None = None) -> list[tuple[str, str]]:
    """(节点, 组) 执行序列。"""
    result = []
    for e in world.timeline.entries:
        if e.kind != KIND_FIRE:
            continue
        if run is None or e.run == run:
            result.append((e.dst_node, e.group))
    return result


def deliveries(world: GraphInstance, src_node: str, src_port: str) -> list:
    """某输出端口投递的载荷序列（观察点收到的值）。"""
    result = []
    for e in world.timeline.entries:
        if e.kind == KIND_DELIVER and e.src_node == src_node and e.src_port == src_port:
            result.append(e.payload)
    return result


def errors(world: GraphInstance) -> list[str]:
    return [e.message for e in world.timeline.entries if e.kind == KIND_ERROR]


def quiesces(world: GraphInstance) -> int:
    return sum(1 for e in world.timeline.entries if e.kind == KIND_QUIESCE)


def node_state(world: GraphInstance, node: str) -> dict:
    return world.observable_state()[node]["state"]


def data_port(world: GraphInstance, node: str, port: str) -> dict:
    return world.observable_state()[node]["data_in"][port]


def trigger_port(world: GraphInstance, node: str, port: str) -> dict:
    return world.observable_state()[node]["trigger_in"][port]


def enable_port(world: GraphInstance, node: str) -> dict:
    return next(iter(world.observable_state()[node]["enable"].values()))


def define_pairing_node() -> NodeType:
    """D1/S1 核心案例的下游节点形态（graph-port-capability-composition.md §4）：
    Data 端口(Replace) + 已连接信号资格端口，数据齐 + 资格即执行。"""
    from eidolon_graph_ref.engine.protocol import TickOutput
    from eidolon_graph_ref.model.node_type import InputGroup, Policy
    from eidolon_graph_ref.model.ports import DataIn, DataOut

    def tick(ctx):
        return TickOutput(data_out={"out": ctx.data_in["data"]})

    return NodeType(
        name="PairingNode",
        data_in=(DataIn("data", qualified=True),),
        data_out=(DataOut("out"),),
        groups=(InputGroup(name="sync", inputs=("data",), policy=Policy.ON_ALL_DATA_READY),),
        tick=tick,
    )


def pairing_types() -> dict:
    return {**PRIMITIVES, "PairingNode": define_pairing_node()}


def build_pairing_graph() -> GraphDefinition:
    """D1/S1 案例的图：上游数据源(in_d)与信号源(in_s)经连线驱动下游配对节点。

    上游每周期产生一个数据和一个翻转电平——与文档 §4 的场景一致
    （注入目标 = 上游入口，数据/信号经真实连线到达下游端口）。
    """
    from eidolon_graph_ref.model.graph import SLOT_QUAL

    g = GraphDefinition("pairing")
    g.add_node("in_d", "Split")  # 数据源入口
    g.add_node("in_s", "DataToSignal", mode="truthy")  # 信号源入口（注入电平数据）
    g.add_node("p", "PairingNode")
    g.add_node("sink", "Sink")
    g.wire("in_d", "out1", "p", "data")
    g.wire("in_s", "level", "p", "data", slot=SLOT_QUAL)
    g.wire("p", "out", "sink", "in")
    return g
