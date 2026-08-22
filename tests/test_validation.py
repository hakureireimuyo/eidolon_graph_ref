"""连线校验:kind 匹配矩阵、扇入禁止、引用存在性、config 分区校验(DSL v2 迁移后)。

依据:graph-node-protocol.md §5 内核边界层 + model/validate.py
"""

import pytest

from eidolon_graph_ref.model.graph import GraphDefinition, SLOT_DATA, SLOT_SIGNAL, SLOT_TRIGGER
from eidolon_graph_ref.model.validate import ValidationError, ensure_valid, validate
from eidolon_primitives import PRIMITIVES


def build_graph(wire_call) -> GraphDefinition:
    g = GraphDefinition("v")
    g.add_node("src", "Source")  # data_out: tick; trigger_in: tick.trigger
    g.add_node("dts", "DataToSignal")  # data_in: convert.data; signal_out: convert
    g.add_node("sink", "Sink")  # data_in: consume.value(纯数据)
    g.add_node("latch", "Latch")  # data_in: release.data(gated); signal_in: release.gate; trigger_in: release.trigger
    wire_call(g)
    return g


# ==================================================================== 合法连接
@pytest.mark.parametrize(
    "wire_call",
    [
        lambda g: g.wire("src", "tick", "sink", "consume.value"),  # Data → Data
        lambda g: g.wire("src", "tick", "latch", "release.trigger", slot=SLOT_TRIGGER),  # Data → TriggerIn(载荷+激活)
        lambda g: g.wire("dts", "convert", "latch", "release.gate", slot=SLOT_SIGNAL),  # Signal → SignalIn(门控)
        lambda g: g.wire("dts", "convert", "latch", "release.trigger", slot=SLOT_TRIGGER),  # Signal → TriggerIn(激活)
    ],
    ids=["data-data", "data-trigger", "signal-gate", "signal-trigger"],
)
def test_legal_wires(wire_call):
    g = build_graph(wire_call)
    result = ensure_valid(g, PRIMITIVES)
    assert result.ok


# ==================================================================== 非法连接
@pytest.mark.parametrize(
    "wire_call",
    [
        lambda g: g.wire("dts", "convert", "sink", "consume.value"),  # Signal → 纯数据端口
        lambda g: g.wire("src", "tick", "latch", "release.gate", slot=SLOT_SIGNAL),  # Data → SignalIn
        lambda g: g.wire("dts", "convert", "latch", "release.data", slot=SLOT_DATA),  # Signal → 数据槽
    ],
    ids=["signal-pure-data", "data-signal-in", "signal-data-slot"],
)
def test_illegal_wires(wire_call):
    g = build_graph(wire_call)
    with pytest.raises(ValidationError):
        ensure_valid(g, PRIMITIVES)


def test_fan_in_forbidden():
    """扇入禁止:每输入端口至多一条线(数据槽/信号槽各自独立)。"""
    g = build_graph(lambda g: None)
    g.add_node("src2", "Source")
    g.wire("src", "tick", "sink", "consume.value")
    g.wire("src2", "tick", "sink", "consume.value")  # 第二条数据线 → 非法
    result = validate(g, PRIMITIVES)
    assert "fan-in forbidden" in result.errors[0]

    g2 = build_graph(lambda g: None)
    g2.add_node("dts2", "DataToSignal")
    g2.wire("dts", "convert", "latch", "release.gate", slot=SLOT_SIGNAL)
    g2.wire("dts2", "convert", "latch", "release.gate", slot=SLOT_SIGNAL)  # 信号槽第二条 → 非法
    result = validate(g2, PRIMITIVES)
    assert "fan-in forbidden" in result.errors[0]


def test_unknown_references():
    g = GraphDefinition("bad")
    g.add_node("n", "NoSuchType")
    assert "unknown node type" in validate(g, PRIMITIVES).errors[0]

    g = GraphDefinition("bad2")
    g.add_node("sink", "Sink")
    g.wire("ghost", "tick", "sink", "consume.value")
    assert "unknown node" in validate(g, PRIMITIVES).errors[0]

    g = GraphDefinition("bad3")
    g.add_node("sink", "Sink")
    g.add_node("src", "Source")
    g.wire("src", "nope", "sink", "consume.value")
    assert "no output port" in validate(g, PRIMITIVES).errors[0]


def test_config_field_validation():
    g = GraphDefinition("cfg")
    g.add_node("sink", "Sink", bogus=1)
    assert "config requires groups/ports/init sections" in validate(g, PRIMITIVES).errors[0]

    g2 = GraphDefinition("cfg2")
    g2.add_node("stod", "SignalToData", unknown_section=1)
    assert "config requires groups/ports/init sections" in validate(g2, PRIMITIVES).errors[0]

    # 按端口名覆盖静态默认值是合法配置(未接线 = 静态,回退端口配置默认值)
    g3 = GraphDefinition("cfg3")
    g3.add_node("stod", "SignalToData", config={"ports": {"pass_value.x": "STATIC_DEFAULT"}})
    assert validate(g3, PRIMITIVES).ok
