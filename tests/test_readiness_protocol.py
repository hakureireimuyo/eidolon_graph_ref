"""Readiness 谓词扩展协议（REFACTOR_READINESS_VALIDATION 中优先级部分）。

谓词协议 = evaluate + explain（调试可视化）+ requires_port_pending（编译期
查询，允许 over-report 不允许 under-report）+ referenced_ports（构建期校验）。
附带：调试模式时间线记录（EIDOLON_DEBUG）与 console 追踪。
"""

import pytest

from eidolon_graph_ref.console import render_readiness_trace
from eidolon_graph_ref.engine import node_semantics
from eidolon_graph_ref.engine.event import Injection, Kind
from eidolon_graph_ref.engine.protocol import GroupOutput
from eidolon_graph_ref.engine.timeline import KIND_READINESS_FAILED
from eidolon_graph_ref.model.graph import GraphDefinition, SLOT_DATA, SLOT_TRIGGER
from eidolon_graph_ref.model.node_type import Group, NodeType
from eidolon_graph_ref.model.ports import DataIn, TriggerIn
from eidolon_graph_ref.model.readiness import ALL, ANY, DATA, TRIGGER

from conftest import make_world


def _data(ports):
    return lambda p: ports.get(p, False)


def _trig(ports):
    return lambda p: ports.get(p, False)


# ---- explain() ---------------------------------------------------------------


def test_explain_all_shows_failing_condition():
    pred = ALL(DATA("g.a"), TRIGGER("g.go"))
    text = pred.explain(_data({"g.a": True}), _trig({"g.go": False}))
    assert "AND failed" in text
    assert "√ DATA('g.a') = True" in text
    assert "× TRIGGER('g.go') = False" in text


def test_explain_any_shows_satisfying_condition():
    pred = ANY(DATA("g.a"), DATA("g.b"))
    text = pred.explain(_data({"g.a": False, "g.b": True}), _trig({}))
    assert "OR: at least one condition met" in text
    assert "× DATA('g.a') = False" in text
    assert "√ DATA('g.b') = True" in text


def test_explain_nested_tree():
    pred = ALL(ANY(DATA("g.a"), DATA("g.b")), TRIGGER("g.go"))
    text = pred.explain(_data({"g.a": False, "g.b": False}), _trig({"g.go": True}))
    assert "AND failed" in text
    assert "× OR: all failed" in text  # 嵌套子式失败逐层可见


# ---- requires_port_pending() -------------------------------------------------


def test_requires_port_pending_leaves_exact():
    assert DATA("g.a").requires_port_pending("g.a") is True
    assert DATA("g.a").requires_port_pending("g.b") is False
    assert TRIGGER("g.go").requires_port_pending("g.go") is True


def test_requires_port_pending_all_any_branch():
    pred = ALL(DATA("g.a"), DATA("g.b"))
    assert pred.requires_port_pending("g.a") is True  # AND: 任一子式要求
    assert pred.requires_port_pending("g.c") is False


def test_requires_port_pending_any_conservative():
    pred = ANY(DATA("g.a"), DATA("g.b"))
    # 保守语义:存在不依赖该端口的分支 → 不要求(假阳性禁止,假阴性允许的另一面)
    assert pred.requires_port_pending("g.a") is False
    same = ANY(DATA("g.a"), ALL(DATA("g.a"), DATA("g.b")))
    assert same.requires_port_pending("g.a") is True  # 所有分支都要求 a
    assert ANY().requires_port_pending("g.a") is False  # 空 ANY


# ---- referenced_ports() 构建期校验 -------------------------------------------


def test_referenced_ports_union():
    pred = ALL(ANY(DATA("g.a"), DATA("g.b")), TRIGGER("g.go"))
    assert pred.referenced_ports() == {"g.a", "g.b", "g.go"}


def test_build_time_readiness_typo_rejected():
    """构建期验证拼写错误:readiness 引用的端口必须属于该组 inputs/triggers。"""
    with pytest.raises(ValueError, match="readiness references non-group port"):
        NodeType(
            name="Typo",
            data_in=(DataIn("a"),),
            groups=(Group("g", inputs=("a",), readiness=DATA("typo"), handler=lambda ctx: None),),
        )
    # 合法构造不抛
    NodeType(
        name="Ok",
        data_in=(DataIn("a"),),
        groups=(Group("g", inputs=("a",), readiness=DATA("a"), handler=lambda ctx: None),),
    )


# ---- 调试模式时间线记录 -------------------------------------------------------


def _gated_node():
    """显式 readiness 的节点类型:ALL(DATA('a'), TRIGGER('go'))。"""

    def h(ctx):
        return GroupOutput()

    nt = NodeType(
        name="GatedSink",
        data_in=(DataIn("a"),),
        trigger_in=(TriggerIn("go"),),
        groups=(Group("g", inputs=("a",), triggers=("go",), readiness=ALL(DATA("a"), TRIGGER("go")), handler=h),),
    )
    g = GraphDefinition("gated")
    g.add_node("n", "GatedSink")
    return g, nt


def test_debug_records_readiness_failures(monkeypatch):
    """EIDOLON_DEBUG 开启:失败的 readiness 评估记录到时间线,含 explain 全文。"""
    monkeypatch.setattr(node_semantics, "RECORD_READINESS_FAILURES", True)
    g, nt = _gated_node()
    world = make_world(g, types={"GatedSink": nt})
    # 只注入 data 不注入 trigger → 唤醒后评估,readiness 失败(触发未到)
    world.run([Injection("n", "a", SLOT_DATA, Kind.DATA, 1)])
    failures = [e for e in world.timeline.entries if e.kind == KIND_READINESS_FAILED]
    assert failures
    entry = failures[0]
    assert entry.dst_node == "n" and entry.group == "g"
    assert "DATA('a') = True" in entry.message and "TRIGGER('go') = False" in entry.message
    trace = render_readiness_trace(world.timeline, "n", "g")
    assert "readiness evaluations" in trace and "epoch 1" in trace


def test_debug_off_timeline_stays_clean(monkeypatch):
    """默认关闭:失败评估同样发生,但时间线不含 readiness_failed 条目。"""
    monkeypatch.setattr(node_semantics, "RECORD_READINESS_FAILURES", False)
    g, nt = _gated_node()
    world = make_world(g, types={"GatedSink": nt})
    world.run([Injection("n", "a", SLOT_DATA, Kind.DATA, 1)])
    assert not [e for e in world.timeline.entries if e.kind == KIND_READINESS_FAILED]
    assert render_readiness_trace(world.timeline, "n", "g") == "√ n.g: readiness never failed"


def test_readiness_protocol_end_to_end():
    """协议扩展不改变既有执行语义:ANY readiness 图照常运转。"""
    g = GraphDefinition("e2e")
    g.add_node("sink", "Sink")
    world = make_world(g)
    world.run([Injection("sink", "consume.value", SLOT_DATA, Kind.DATA, 1)])
    view = world.observable_state()["sink"]
    assert view["state"]["last"] == 1
    assert view["data_in"]["consume.value"]["pending"] is False
