"""端口状态统一（REFACTOR_PORT_STATE_UNIFICATION）：PortState = 不变式 + 运行事实。

三套冗余状态机（Data/Signal/Trigger）统一为单一 PortState：
- PortInvariants（frozen）：构建期一次决定、构造时校验、运行时只读
- RuntimeFacts：value / level / pending——三种端口共享的可变事实
- event_driven / has_value 是粘性锁存：消费即清空 pending_deliveries，
  资格必须跨 epoch 保持，故不能由 pending 推导（草案从 pending 推导有误）
"""

import pytest

from eidolon_graph_ref.engine.event import Event, Injection, Kind
from eidolon_graph_ref.engine.port_state import PortInvariants, PortState
from eidolon_graph_ref.model.graph import GraphDefinition, SLOT_DATA, SLOT_TRIGGER
from eidolon_graph_ref.model.ports import APPEND, REPLACE

from conftest import make_world


def _state(port_type="data", wired=False, cache_strategy=REPLACE) -> PortState:
    return PortState(
        PortInvariants(
            port_type=port_type,
            is_wired=wired,
            cache_strategy=cache_strategy if port_type == "data" else None,
        )
    )


def _delivery(eid=1, slot=SLOT_DATA):
    from eidolon_graph_ref.engine.event import Delivery

    return Delivery(eid, "sink", "in.value", slot, seq=1)


def test_invariant_validation():
    """不变式在构造时校验：data 必须有缓存策略,非 data 不得携带。"""
    with pytest.raises(ValueError, match="data port must specify"):
        PortInvariants(port_type="data", is_wired=False)
    with pytest.raises(ValueError, match="cannot have cache_strategy"):
        PortInvariants(port_type="signal", is_wired=False, cache_strategy=REPLACE)
    PortInvariants(port_type="signal", is_wired=False)  # 合法


def test_shared_invariants_deduplicates_by_value():
    """不变式共享工厂：同值返回同一对象(内存验收点)。"""
    from eidolon_graph_ref.engine.port_state import shared_invariants

    a = shared_invariants("data", True, REPLACE)
    b = shared_invariants("data", True, REPLACE)
    assert a is b
    assert shared_invariants("signal", False) is not a
    with pytest.raises(ValueError):
        shared_invariants("data", True)  # data 必须给缓存策略


def test_event_driven_is_sticky_latch():
    """event_driven 只升不降：消费清空 pending 后动态资格仍保持。"""
    state = _state()
    assert state.event_driven is False
    state.receive(Event(1, 1, Kind.DATA, 42, "host", None), _delivery())
    assert state.event_driven is True
    # 消费后 pending_deliveries 清空——若由 pending 推导会错误回退
    state.pending_deliveries = []
    state.facts.pending = False
    assert state.event_driven is True


def test_receive_polymorphism_by_port_type():
    """receive() 按 port_type 分派：data 缓存值 / signal 电平 / trigger 载荷。"""
    data = _state("data")
    data.receive(Event(1, 1, Kind.DATA, 42, "host", None), _delivery())
    assert data.facts.value == 42 and data.facts.level is None

    sig = _state("signal")
    sig.receive(Event(1, 1, Kind.SIGNAL, True, "host", None), _delivery(1, "signal"))
    assert sig.facts.level is True and sig.facts.value is None

    trig = _state("trigger")
    trig.receive(Event(1, 1, Kind.DATA, 7, "host", None), _delivery(1, "trigger"))
    assert trig.facts.value == 7 and trig.has_payload is True
    # 信号事件到触发槽：纯激活,无载荷
    trig2 = _state("trigger")
    trig2.receive(Event(2, 1, Kind.SIGNAL, True, "host", None), _delivery(2, "trigger"))
    assert trig2.has_payload is False and trig2.facts.value is None


def test_cache_append_accumulates():
    """APPEND 端口累积列表;静态默认值若非列表,作为累积起点规范化。"""
    state = _state("data", cache_strategy=APPEND)
    state.receive(Event(1, 1, Kind.DATA, 1, "host", None), _delivery())
    state.receive(Event(2, 1, Kind.DATA, 2, "host", None), _delivery(2))
    assert state.facts.value == [1, 2]

    seeded = PortState(
        PortInvariants(port_type="data", is_wired=False, cache_strategy=APPEND),
        has_value=True,
    )
    seeded.facts.value = 0  # 静态默认非列表
    seeded.receive(Event(3, 1, Kind.DATA, 1, "host", None), _delivery(3))
    assert seeded.facts.value == [0, 1]


def test_unified_states_drive_execution():
    """端到端：统一 PortState 驱动真实图执行(静态/动态/门控路径)。"""
    g = GraphDefinition("unified")
    g.add_node("src", "Source")
    g.add_node("sink", "Sink")
    g.wire("src", "tick", "sink", "consume.value")
    world = make_world(g)

    state = world.data_states["sink"]["consume.value"]
    assert state.port_type == "data"
    assert state.is_wired is True and state.event_driven is True
    assert state.cache_strategy == REPLACE

    world.run([Injection("src", "tick.trigger", SLOT_TRIGGER, Kind.SIGNAL, True)])
    assert state.has_value is True
    assert state.facts.value == 0  # Source 首轮返回 0
    assert state.facts.pending is False  # 已消费
    assert state.event_driven is True  # 锁存保持
