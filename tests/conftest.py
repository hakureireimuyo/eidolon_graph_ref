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
