"""内核自举边界:内核不拥有任何节点——内置节点与外部节点同为协议实现者。

验证命题(graph-node-protocol.md §8):

> Graph Kernel 本身不需要认识任何具体节点类型;所有节点,包括原本由内核
> 提供的基础节点,都可以通过 Node Protocol 注册进入运行平面。

锁定三条:
1. 内核包源码零 primitives 引用,无 primitives 子模块——内置节点是包概念
   不是内核概念(§8);
2. 内置包只提供 NodeType 值,注册 = 宿主把值放进 types 字典(§8);
3. 内置节点与外部孪生声明在 Kernel 侧无任何区别:同一图、同一注入 →
   时间线/状态逐位一致——ABI 是节点的唯一接口,不是"外部节点接口"。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import eidolon_graph_ref
from eidolon_graph_ref.engine.event import Injection, Kind
from eidolon_graph_ref.engine.protocol import TickOutput
from eidolon_graph_ref.model.graph import GraphDefinition, SLOT_TRIGGER
from eidolon_graph_ref.model.node_type import NodeType
from eidolon_graph_ref.model.ports import DataOut
from eidolon_primitives import PRIMITIVES

from conftest import make_world


# ==================================================================== 内核零内置知识
def test_kernel_package_carries_no_primitives():
    """内核包源码零 primitives 引用、无 primitives 子模块:内置节点不属于内核(§8)。"""
    for entry in eidolon_graph_ref.__path__:
        pkg_dir = Path(entry)
        assert (pkg_dir / "primitives").exists() is False  # 无 primitives 子目录
        for py in pkg_dir.rglob("*.py"):
            src = py.read_text(encoding="utf-8")
            assert "primitives" not in src, f"kernel package file references primitives: {py}"
    assert not hasattr(eidolon_graph_ref, "primitives")  # 无 primitives 子模块属性


# ==================================================================== 内置包 = NodeType 值集合
def test_primitives_are_plain_nodetypes():
    """内置包只提供 NodeType 值:注册 = 宿主把值放进 types 字典(§8)。"""
    assert len(PRIMITIVES) == 10
    for name, t in PRIMITIVES.items():
        assert isinstance(t, NodeType), name
        assert t.name == name


# ==================================================================== 内置 vs 外部孪生等价
def test_builtin_and_external_twin_indistinguishable():
    """同一图、同一注入:内置 Source 与外部孪生声明 → 时间线/状态逐位一致(§8)。

    内核侧对"内置"与"外部"零区分——ABI 是节点的唯一接口,不是外部节点的
    专用入口。孪生声明与内置 Source 声明逐字段相同,只换类型名(类型名是
    宿主注册表概念,非内核事实)。
    """

    def twin_tick(ctx):
        count = ctx.state["count"]
        return TickOutput(data_out={"out": count}, state={"count": count + ctx.config["step"]})

    TwinSource = NodeType(
        name="TwinSource",
        data_out=(DataOut("out"),),
        state_defaults={"count": 0},
        config_defaults={"step": 1},
        tick=twin_tick,
    )

    def build_graph(type_name: str) -> GraphDefinition:
        g = GraphDefinition("twin")
        g.add_node("src", type_name, step=2)
        g.add_node("buf", "Buffer")
        g.add_node("sink", "Sink")
        g.wire("src", "out", "buf", "put")
        g.wire("buf", "out", "sink", "in")
        return g

    def run_and_snapshot(type_name: str, types) -> tuple:
        world = make_world(build_graph(type_name), types)
        world.run()
        world.run()
        world.run([Injection("buf", "flush", SLOT_TRIGGER, Kind.SIGNAL, True)])
        obs = world.observable_state()
        for view in obs.values():
            view.pop("type", None)  # 类型名 = 宿主注册表概念,非内核事实
        return (obs, [repr(e) for e in world.timeline.entries])

    builtin_snap = run_and_snapshot("Source", {**PRIMITIVES})
    external_snap = run_and_snapshot("TwinSource", {**PRIMITIVES, "TwinSource": TwinSource})
    assert builtin_snap == external_snap  # 时间线 + 状态逐位一致
    # epoch 3 内注入先于源节点播种:flush 时 buf 已累积 [0, 2](4 是 flush 后播种的)
    assert builtin_snap[0]["sink"]["state"]["last"] == [0, 2]  # 运行确有事实发生
