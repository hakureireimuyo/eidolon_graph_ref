"""语义验证矩阵:事件解释矩阵(NodeSemantics final)中尚未被既有测试逐项锁死的场景。

- Trigger 连续 N 次:同一 run 内组只执行一次(turns 预算),未消费触发保持 pending
- 一个 Event 部分消费:status=pending(消费侧标记、未消费侧保留)
- handler 异常:同 run 不重试(turns 已消耗),跨 run 唤醒重试成功
"""

from eidolon_graph_ref.engine.event import Injection, Kind
from eidolon_graph_ref.engine.protocol import GroupOutput
from eidolon_graph_ref.engine.timeline import KIND_ERROR
from eidolon_graph_ref.model.graph import GraphDefinition, SLOT_DATA, SLOT_SIGNAL, SLOT_TRIGGER
from eidolon_graph_ref.model.node_type import Group, NodeType
from eidolon_graph_ref.model.ports import DataIn, DataOut
from eidolon_primitives import PRIMITIVES

from conftest import fired, make_world, node_state


# ==================================================================== Trigger 折叠
def test_trigger_three_activations_fold_into_one_fire():
    """Trigger 连续 3 次:同一 run 内组只执行一次;未消费的触发事件保持 pending。"""
    g = GraphDefinition("fold")
    g.add_node("in_d", "Split")
    g.add_node("latch", "Latch")
    g.add_node("sink", "Sink")
    g.wire("in_d", "fan.out1", "latch", "release.data")
    g.wire("latch", "release", "sink", "consume.value")
    world = make_world(g)
    world.run([Injection("in_d", "fan.value", SLOT_DATA, Kind.DATA, 42)])
    assert ("latch", "release") not in fired(world, 1)  # 数据齐但无触发 → 不执行
    world.run(
        [
            Injection("latch", "release.trigger", SLOT_TRIGGER, Kind.SIGNAL, True),
            Injection("latch", "release.trigger", SLOT_TRIGGER, Kind.SIGNAL, True),
            Injection("latch", "release.trigger", SLOT_TRIGGER, Kind.SIGNAL, True),
        ]
    )
    assert len([f for f in fired(world, 2) if f == ("latch", "release")]) == 1  # 折叠为一次
    trig_events = [e for e in world.timeline.events.values() if e.port == "release.trigger"]
    assert all(e.status == "consumed" for e in trig_events)  # 注入先于访问:3 事件一并消费
    assert all(len(e.consumed_by) == 1 for e in trig_events)  # 身份逐一保留
    assert node_state(world, "sink")["last"] == 42


# ==================================================================== 部分消费
def test_partial_consumption_event_stays_pending():
    """一个 Event 扇出两处,仅一处消费:status=pending,消费侧标记、未消费侧保留。"""
    g = GraphDefinition("partial")
    g.add_node("gate", "DataToSignal", config={"groups": {"convert": {"mode": "truthy"}}})
    g.add_node("split", "Split")
    g.add_node("s1", "Sink")  # 消费侧
    g.add_node("latch2", "Latch")  # gate LOW + 无触发:接收缓存但不消费
    g.wire("split", "fan.out1", "s1", "consume.value")
    g.wire("split", "fan.out1", "latch2", "release.data")
    g.wire("gate", "convert", "latch2", "release.gate", slot=SLOT_SIGNAL)
    world = make_world(g)
    world.run([Injection("gate", "convert.data", SLOT_DATA, Kind.DATA, 0)])  # gate LOW
    world.run([Injection("split", "fan.value", SLOT_DATA, Kind.DATA, 7)])
    ev = next(e for e in world.timeline.events.values() if e.producer == "split" and e.port == "fan.out1")
    assert len(ev.deliveries) == 2  # 一次产出,两处投递
    assert ev.status == "pending"  # 部分消费 → 未全部消费
    by_node = {d.node: d.consumed_seq for d in ev.deliveries}
    assert by_node["s1"] is not None  # 消费侧 Delivery 已标记
    assert by_node["latch2"] is None  # 无触发未消费侧保留
    assert {c[1] for c in ev.consumed_by} == {"s1"}


# ==================================================================== handler 异常重试
def test_exception_retries_next_run():
    """handler 异常:同一 run 内组只执行一次(turns 已消耗)→ 不重试;
    下一 run 唤醒重试成功。"""
    flaky = {"fail": True}

    def run_go(ctx):
        if flaky["fail"]:
            flaky["fail"] = False
            raise RuntimeError("boom")
        return GroupOutput(data_out={"go": ctx.data_in["go.in"]})

    flaky_type = NodeType(
        name="Flaky",
        data_in=(DataIn("go.in"),),
        data_out=(DataOut("go"),),
        groups=(Group("go", inputs=("go.in",), outputs=("go",), handler=run_go),),
    )
    g = GraphDefinition("retry")
    g.add_node("bad", "Flaky")
    g.add_node("sink", "Sink")
    g.wire("bad", "go", "sink", "consume.value")
    world = make_world(g, {**PRIMITIVES, "Flaky": flaky_type})
    world.run(
        [
            Injection("bad", "go.in", SLOT_DATA, Kind.DATA, 1),
            Injection("bad", "go.in", SLOT_DATA, Kind.DATA, 2),
        ]
    )
    # 同 run:异常只发生一次(第二次唤醒被 turns 挡住),pending 保留
    assert len([e for e in world.timeline.entries if e.kind == KIND_ERROR]) == 1
    world.run([Injection("bad", "go.in", SLOT_DATA, Kind.DATA, 3)])
    # 下一 run:turns 重置,pending 保留的旧数据 + 新注入重新唤醒 → 重试成功
    assert node_state(world, "sink")["last"] == 3
    assert len([f for f in fired(world, 2) if f[0] == "bad"]) == 1
