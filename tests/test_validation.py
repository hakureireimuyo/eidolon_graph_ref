"""连线校验：kind 匹配矩阵、扇入禁止、引用存在性。

依据：graph-ports-bindings.md §4.4 连线校验表 + §4.3 扇入禁止
"""

import pytest

from eidolon_graph_ref.model.graph import GraphDefinition, SLOT_DATA, SLOT_QUAL, SLOT_SIGNAL, SLOT_TRIGGER
from eidolon_graph_ref.model.validate import ValidationError, ensure_valid, validate
from eidolon_primitives import PRIMITIVES


def build_graph(wire_call) -> GraphDefinition:
    g = GraphDefinition("v")
    g.add_node("src", "Source")  # data_out: out
    g.add_node("dts", "DataToSignal")  # data_in: data; signal_out: level
    g.add_node("sink", "Sink")  # data_in: in (无资格槽)
    g.add_node("latch", "Latch")  # data_in: data(资格槽); trigger_in: release
    wire_call(g)
    return g


# ==================================================================== 合法连接
@pytest.mark.parametrize(
    "wire_call",
    [
        lambda g: g.wire("src", "out", "sink", "in"),  # Data → Data
        lambda g: g.wire("src", "out", "latch", "release", slot=SLOT_TRIGGER),  # Data → TriggerIn(载荷+激活)
        lambda g: g.wire("dts", "level", "sink", "enable", slot=SLOT_SIGNAL),  # Signal → SignalIn(enable)
        lambda g: g.wire("dts", "level", "latch", "data", slot=SLOT_QUAL),  # Signal → 资格槽
        lambda g: g.wire("dts", "level", "latch", "release", slot=SLOT_TRIGGER),  # Signal → TriggerIn(激活)
    ],
    ids=["data-data", "data-trigger", "signal-enable", "signal-qual", "signal-trigger"],
)
def test_legal_wires(wire_call):
    g = build_graph(wire_call)
    result = ensure_valid(g, PRIMITIVES)
    assert result.ok


# ==================================================================== 非法连接
@pytest.mark.parametrize(
    "wire_call",
    [
        lambda g: g.wire("dts", "level", "sink", "in"),  # Signal → 纯数据端口(无资格槽)
        lambda g: g.wire("src", "out", "sink", "enable", slot=SLOT_SIGNAL),  # Data → SignalIn
        lambda g: g.wire("src", "out", "latch", "data", slot=SLOT_QUAL),  # Data → 资格槽
        lambda g: g.wire("dts", "level", "latch", "data", slot=SLOT_DATA),  # Signal → 数据槽
    ],
    ids=["signal-pure-data", "data-enable", "data-qual", "signal-data-slot"],
)
def test_illegal_wires(wire_call):
    g = build_graph(wire_call)
    with pytest.raises(ValidationError):
        ensure_valid(g, PRIMITIVES)


def test_fan_in_forbidden():
    """扇入禁止：每输入端口至多一条数据线、资格槽至多一条信号线。"""
    g = build_graph(lambda g: None)
    g.add_node("src2", "Source")
    g.wire("src", "out", "sink", "in")
    g.wire("src2", "out", "sink", "in")  # 第二条数据线 → 非法
    result = validate(g, PRIMITIVES)
    assert "fan-in forbidden" in result.errors[0]

    g2 = build_graph(lambda g: None)
    g2.add_node("dts2", "DataToSignal")
    g2.wire("dts", "level", "latch", "data", slot=SLOT_QUAL)
    g2.wire("dts2", "level", "latch", "data", slot=SLOT_QUAL)  # 资格槽第二条 → 非法
    result = validate(g2, PRIMITIVES)
    assert "fan-in forbidden" in result.errors[0]


def test_unknown_references():
    g = GraphDefinition("bad")
    g.add_node("n", "NoSuchType")
    assert "unknown node type" in validate(g, PRIMITIVES).errors[0]

    g = GraphDefinition("bad2")
    g.add_node("sink", "Sink")
    g.wire("ghost", "out", "sink", "in")
    assert "unknown src node" in validate(g, PRIMITIVES).errors[0]

    g = GraphDefinition("bad3")
    g.add_node("sink", "Sink")
    g.add_node("src", "Source")
    g.wire("src", "nope", "sink", "in")
    assert "no output port" in validate(g, PRIMITIVES).errors[0]


def test_config_field_validation():
    g = GraphDefinition("cfg")
    g.add_node("sink", "Sink", bogus=1)
    assert "unknown config field" in validate(g, PRIMITIVES).errors[0]

    # 按端口名覆盖静态默认值是合法配置（"可选参数:未接线 = 静态(回退配置默认值)"）
    g2 = GraphDefinition("cfg2")
    g2.add_node("stod", "SignalToData", x="STATIC_DEFAULT")
    assert validate(g2, PRIMITIVES).ok
