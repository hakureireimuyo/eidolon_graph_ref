"""语义验证矩阵:内核冻结评审中尚未被既有测试逐项锁死的场景。

来源:最小验证内核评审结论的验证矩阵(17 行)。既有测试已覆盖大多数行
(扇出/REPLACE/配对序列/Enable 门控/epoch 边界/NodeTurn 预算/孤儿事件等),
本文件锁定四个尚未有直接测试的语义点:

- Trigger 连续 N 次:activation 折叠为一次执行,事件身份逐一保留
- 一个 Event 部分消费:status=pending(消费侧标记、未消费侧保留)
- tick 异常:同 epoch 不重试(turn 已消耗),跨 epoch 唤醒重试成功
- LOW Data 自消费:Delivery.consumed_seq 与时间线/consumed_by 一致
"""

from eidolon_graph_ref.engine.event import Injection, Kind
from eidolon_graph_ref.engine.protocol import TickOutput
from eidolon_graph_ref.engine.timeline import KIND_ERROR
from eidolon_graph_ref.model.graph import GraphDefinition, SLOT_DATA, SLOT_QUAL, SLOT_TRIGGER
from eidolon_graph_ref.model.node_type import InputGroup, NodeType, Policy
from eidolon_graph_ref.model.ports import DataIn, DataOut
from eidolon_primitives import PRIMITIVES

from conftest import build_pairing_graph, fired, make_world, node_state, pairing_types

PAIRING_TYPES = pairing_types()


# ==================================================================== Trigger 折叠
def test_trigger_three_activations_fold_into_one_fire():
    """Trigger 连续 3 次:activation 折叠为一次执行;3 个事件身份逐一保留并一并消费。"""
    g = GraphDefinition("fold")
    g.add_node("in_d", "Split")
    g.add_node("latch", "Latch")
    g.add_node("sink", "Sink")
    g.wire("in_d", "out1", "latch", "data")
    g.wire("latch", "out", "sink", "in")
    world = make_world(g)
    world.run([Injection("in_d", "in", SLOT_DATA, Kind.DATA, 42)])
    assert ("latch", "release") not in fired(world, 1)  # 数据齐但无触发 → 不执行
    world.run(
        [
            Injection("latch", "release", SLOT_TRIGGER, Kind.SIGNAL, True),
            Injection("latch", "release", SLOT_TRIGGER, Kind.SIGNAL, True),
            Injection("latch", "release", SLOT_TRIGGER, Kind.SIGNAL, True),
        ]
    )
    assert len([f for f in fired(world, 2) if f == ("latch", "release")]) == 1  # 折叠为一次
    trig_events = [e for e in world.timeline.events.values() if e.port == "release"]
    assert all(e.status == "consumed" for e in trig_events)  # 3 事件一并消费
    assert all(len(e.consumed_by) == 1 for e in trig_events)  # 身份逐一保留
    assert node_state(world, "sink")["last"] == 42


# ==================================================================== 部分消费
def test_partial_consumption_event_stays_pending():
    """一个 Event 扇出两处,仅一处消费:status=pending,消费侧标记、未消费侧保留。"""
    g = GraphDefinition("partial")
    g.add_node("gate", "DataToSignal", mode="truthy")
    g.add_node("split", "Split")
    g.add_node("s1", "Sink")  # 消费侧
    g.add_node("s2", "Sink")  # enable LOW:接收缓存但不消费
    g.wire("split", "out1", "s1", "in")
    g.wire("split", "out1", "s2", "in")
    g.wire("gate", "level", "s2", "enable")
    world = make_world(g)
    world.run([Injection("gate", "data", SLOT_DATA, Kind.DATA, 0)])  # s2 enable LOW
    world.run([Injection("split", "in", SLOT_DATA, Kind.DATA, 7)])
    ev = next(e for e in world.timeline.events.values() if e.producer == "split" and e.port == "out1")
    assert len(ev.deliveries) == 2  # 一次产出,两处投递
    assert ev.status == "pending"  # 部分消费 → 未全部消费
    by_node = {d.node: d.consumed_seq for d in ev.deliveries}
    assert by_node["s1"] is not None  # 消费侧 Delivery 已标记
    assert by_node["s2"] is None  # 门控未消费侧保留
    assert {c[1] for c in ev.consumed_by} == {"s1"}


# ==================================================================== tick 异常重试
def test_exception_retries_next_epoch():
    """tick 异常:turns 已消耗 → 同 epoch 不重试;下一 epoch 唤醒重试成功。"""
    flaky = {"fail": True}

    def tick(ctx):
        if flaky["fail"]:
            flaky["fail"] = False
            raise RuntimeError("boom")
        return TickOutput(data_out={"out": ctx.data_in["in"]})

    flaky_type = NodeType(
        name="Flaky",
        data_in=(DataIn("in"),),
        data_out=(DataOut("out"),),
        groups=(InputGroup("go", inputs=("in",), policy=Policy.ON_ANY_DATA),),
        tick=tick,
    )
    g = GraphDefinition("retry")
    g.add_node("bad", "Flaky")
    g.add_node("sink", "Sink")
    g.wire("bad", "out", "sink", "in")
    world = make_world(g, {**PRIMITIVES, "Flaky": flaky_type})
    world.run(
        [
            Injection("bad", "in", SLOT_DATA, Kind.DATA, 1),
            Injection("bad", "in", SLOT_DATA, Kind.DATA, 2),
        ]
    )
    # 同 epoch:异常只发生一次(第二次唤醒被 turn 预算挡住),pending 保留
    assert len([e for e in world.timeline.entries if e.kind == KIND_ERROR]) == 1
    world.run([Injection("bad", "in", SLOT_DATA, Kind.DATA, 3)])
    # 下一 epoch:预算重置,pending 保留的旧数据 + 新注入重新唤醒 → 重试成功
    assert node_state(world, "sink")["last"] == 3
    assert len([f for f in fired(world, 2) if f[0] == "bad"]) == 1


# ==================================================================== LOW 自消费一致性
def d(world, value):
    """数据到达(经数据源入口注入)。"""
    world.run([Injection("in_d", "in", SLOT_DATA, Kind.DATA, value)])


def s(world, level):
    """信号到达(经信号源入口注入电平数据)。"""
    world.run([Injection("in_s", "data", SLOT_DATA, Kind.DATA, level)])


def test_low_self_consume_marks_delivery_consumed():
    """LOW 期间到达的数据自消费:Delivery.consumed_seq 与时间线/consumed_by 一致。

    投递记录先于到达即消费的记录入档(因果序 deliver → consume);本次投递
    在自消费时已被标记,Event.status 应判 consumed 而非永远 pending。
    """
    world = make_world(build_pairing_graph, PAIRING_TYPES)
    s(world, False)  # 资格 LOW
    d(world, "D")  # LOW 期间数据到达 → 自消费
    ev = next(e for e in world.timeline.events.values() if e.producer == "in_d")
    assert ev.consumed_by  # 自消费已记录
    dl = ev.deliveries[0]
    assert dl.consumed_seq is not None  # 本次投递已标记
    assert dl.consumed_seq > dl.seq  # 因果序:投递先于消费
    assert dl.consumed_seq == ev.consumed_by[0][0]  # 与消费记录同一 seq
    assert ev.status == "consumed"  # 事件已消费
