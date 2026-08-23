"""构建管线回归测试:2026-08-23 同步审查发现的偏离修复锁定。

- 资产构建期 isinstance 类型校验(graph-assets.md §8:resolve → isinstance → 注入)
- init 返回未知状态字段 / 不可复制值 → BuildReport error(node-protocol.md §7)
- 注入按注入序入队(node-protocol.md §3.2;叠加 NodeTurn 预算,顺序即语义)
- 端口跨类别同名 = IR 非法(_input_state 信号优先会遮蔽同名 DataIn)
- 绑定信号的控制消费记录 KIND_CONSUME(消费因果进入时间线)
- observable_state 暴露资产结构事实(ref/resolved),不暴露对象(graph-assets.md §8)
"""

import pytest

from eidolon_graph_ref.engine import GraphInstance, Injection, Kind
from eidolon_graph_ref.engine.timeline import KIND_CONSUME, KIND_FIRE
from eidolon_graph_ref.model import GraphDefinition, SLOT_DATA, SLOT_SIGNAL, SLOT_TRIGGER
from eidolon_graph_ref.model.assets import AssetIn, AssetRef
from eidolon_graph_ref.model.node_type import Group, NodeType
from eidolon_graph_ref.model.ports import DataIn, DataOut, SignalIn, SignalOut, TriggerIn
from eidolon_primitives import PRIMITIVES

from conftest import fired, make_world, node_state
from fake_assets import DatabaseCapability, FakeAssetSystem


def _asset_node(asset_in):
    return NodeType(
        name="AssetUser",
        asset_in=asset_in,
        data_in=(DataIn("go.in"),),
        groups=(Group("go", inputs=("go.in",), handler=lambda ctx: None),),
    )


# ==================================================================== 资产 isinstance 校验
def test_wrong_type_asset_is_build_error():
    """解析成功但类型不满足 Capability 接口 → BuildReport error(声明即必须的类型面)。"""
    system = FakeAssetSystem()
    cache_ref = system.create_cache("uri")  # FakeCache:有 get,无 query
    g = GraphDefinition()
    g.add_node("n", "AssetUser")
    g.bind_asset("n", "db", cache_ref.asset_id)
    report = GraphInstance.build(g, {"AssetUser": _asset_node((AssetIn("db", DatabaseCapability),))}, asset_resolver=system)
    assert not report.ok
    assert "expected" in report.errors[0] and "DatabaseCapability" in report.errors[0]
    assert report.instance is None


def test_none_type_asset_skips_type_check():
    """AssetIn.type = None → 不做类型检查(声明即必须只要求绑定 + 解析成功)。"""
    system = FakeAssetSystem()
    cache_ref = system.create_cache("uri")
    g = GraphDefinition()
    g.add_node("n", "AssetUser")
    g.bind_asset("n", "db", cache_ref.asset_id)
    report = GraphInstance.build(g, {"AssetUser": _asset_node((AssetIn("db", None),))}, asset_resolver=system)
    assert report.ok, report.errors


# ==================================================================== init 校验
def _init_node(init, init_defaults=None):
    return NodeType(
        name="WithInit",
        data_in=(DataIn("go.in"),),
        state_defaults={"real": 0},
        init_defaults=dict(init_defaults or {}),
        groups=(Group("go", inputs=("go.in",), handler=lambda ctx: None),),
        init=init,
    )


def _build_init(init, config=None, init_defaults=None):
    g = GraphDefinition()
    g.add_node("n", "WithInit", config=config or {})
    return GraphInstance.build(g, {"WithInit": _init_node(init, init_defaults)})


def test_init_unknown_state_field_is_build_error():
    """init 返回未知状态字段 → BuildReport error,字段不得泄漏进 node_states。"""
    report = _build_init(lambda ctx: {"ghost": 1})
    assert not report.ok
    assert "unknown state fields" in report.errors[0]


def test_init_non_copyable_delta_is_build_error():
    """init 返回不可复制值(锁/连接类能力对象)→ BuildReport error(值域 = Value)。"""
    import threading

    report = _build_init(lambda ctx: {"real": threading.Lock()})
    assert not report.ok
    assert "init raised" in report.errors[0]


def test_init_valid_delta_merges_into_initial_state():
    """合法 init:初始状态 = state_defaults ⊕ init 增量;config init 节参与合并。"""
    report = _build_init(lambda ctx: {"real": ctx.config["seed"] + 1}, config={"init": {"seed": 41}}, init_defaults={"seed": 0})
    assert report.ok, report.errors
    assert node_state(report.instance, "n")["real"] == 42


# ==================================================================== 注入按注入序入队
def test_injections_are_visited_in_injection_order():
    """同批注入:下游在先、上游在后 → 注入序访问,先注入的事实先被执行。

    FIFO 下 sink 先消费 'direct';split 传播到达时 sink 的 NodeTurn 预算已耗,
    'via' 保持 pending 到后续 epoch。逆序(修复前)会折叠成一次 fire 且
    先注入的 'direct' 被后到的 'via' 覆盖——顺序经预算放大为语义差异(§3.2)。
    """
    g = GraphDefinition("order")
    g.add_node("split", "Split")
    g.add_node("sink", "Sink")
    g.wire("split", "fan.out1", "sink", "consume.value")
    world = make_world(g)
    world.run([
        Injection("sink", "consume.value", SLOT_DATA, Kind.DATA, "direct"),  # 下游先注入
        Injection("split", "fan.value", SLOT_DATA, Kind.DATA, "via"),  # 上游后注入
    ])
    sink_fires = [f for f in fired(world, 1) if f == ("sink", "consume")]
    assert len(sink_fires) == 1  # 预算:每 epoch 至多一次
    assert node_state(world, "sink")["last"] == "direct"  # 先注入先执行
    view = world.observable_state()["sink"]["data_in"]["consume.value"]
    assert view["pending"] is True and view["value"] == "via"  # 后到事实待下一 epoch


# ==================================================================== 端口跨类别同名 = IR 非法
def test_ir_rejects_duplicate_input_port_name_across_categories():
    with pytest.raises(ValueError, match="duplicate input port name"):
        NodeType(
            name="X",
            data_in=(DataIn("x"),),
            signal_in=(SignalIn("x"),),
            groups=(Group("g", inputs=("x",), handler=lambda ctx: None),),
        )


def test_ir_rejects_duplicate_output_port_name_across_categories():
    with pytest.raises(ValueError, match="duplicate output port name"):
        NodeType(
            name="X",
            data_in=(DataIn("x"),),
            data_out=(DataOut("o"),),
            signal_out=(SignalOut("o"),),
            groups=(Group("g", inputs=("x",), outputs=("o",), handler=lambda ctx: None),),
        )


# ==================================================================== 控制消费进入时间线
def test_bound_signal_settle_records_kind_consume():
    """绑定信号的 pending 在唤醒时按控制态消费:KIND_CONSUME 条目,consumed_seq 指向它。"""
    g = GraphDefinition("settle")
    g.add_node("latch", "Latch")
    world = make_world(g)
    world.run([Injection("latch", "release.gate", SLOT_SIGNAL, Kind.SIGNAL, False)])
    consume_entries = [e for e in world.timeline.entries if e.kind == KIND_CONSUME]
    assert len(consume_entries) == 1
    entry = consume_entries[0]
    assert (entry.dst_node, entry.dst_port) == ("latch", "release.gate")
    ev = world.timeline.events[1]
    assert entry.consumed == (ev.id,)
    assert ev.consumed_by == [(entry.seq, "latch", "release.gate")]
    assert ev.deliveries[0].consumed_seq == entry.seq
    assert not [e for e in world.timeline.entries if e.kind == KIND_FIRE]  # 无执行


# ==================================================================== observable_state 资产结构事实
def test_observable_state_exposes_asset_structure_not_objects():
    """观察面只含 ref/resolved 结构事实,绝不暴露能力对象本身。"""
    system = FakeAssetSystem()
    ref = system.create_db("uri")
    g = GraphDefinition()
    g.add_node("n", "AssetUser")
    g.bind_asset("n", "db", ref.asset_id)
    report = GraphInstance.build(g, {"AssetUser": _asset_node((AssetIn("db", DatabaseCapability),))}, asset_resolver=system)
    assert report.ok, report.errors
    view = report.instance.observable_state()["n"]
    assert view["assets"] == {"db": {"ref": ref.asset_id, "resolved": True}}
    assert system.instance(ref.asset_id) not in view["assets"].values()  # 无对象泄漏
