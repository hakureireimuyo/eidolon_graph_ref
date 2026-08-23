"""组合链验证：Source→Buffer→Join→Split→Latch→DataToSignal→SignalToData→Sink。

《架构验证性重写》的验收场景：用少量 Primitive Node 把核心机制组合起来，
通过控制台输出直接观察事件传递过程（事件身份、谁生产、谁消费）。

拓扑（DSL v2 组限定端口名："{组}.{参数}"；单输出端口名 = 组名）：
    src.tick ──→ buf.put.item            const.tick ──→ join.join.b
    buf.flush ──→ join.join.a
    join.join ──→ split.fan.value ──fan.out1──→ latch.release.data
                                  └─fan.out2──→ probe.observe.value
    latch.release ──→ dts.convert.data(DataToSignal)
    dts.convert ──┬──→ stod.pass_value.gate[signal]   (信号两重语义分别消费:level 状态 / occurrence 激活)
                  └──→ stod.pass_value.pass[trigger]
    stod.pass_value ──→ sink.consume.value

运行：uv run python examples/validation_chain.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eidolon_graph_ref.console import render_epoch, render_event_archive, render_state
from eidolon_graph_ref.engine.event import Injection, Kind
from eidolon_graph_ref.model.graph import GraphDefinition, SLOT_SIGNAL, SLOT_TRIGGER
from eidolon_graph_ref.model.validate import ensure_valid
from eidolon_graph_ref.engine.instance import GraphInstance
from eidolon_primitives import PRIMITIVES


def build() -> GraphDefinition:
    g = GraphDefinition("validation-chain")
    g.add_node("src", "Source")
    g.add_node("const", "Constant", config={"groups": {"tick": {"value": 10}}})
    g.add_node("buf", "Buffer")
    g.add_node("join", "Join")
    g.add_node("split", "Split")
    g.add_node("latch", "Latch")
    g.add_node("probe", "Probe")
    g.add_node("dts", "DataToSignal", config={"groups": {"convert": {"mode": "truthy"}}})
    # x 按端口名配置覆盖静态默认值：信号未激活时放行的受控默认参数
    g.add_node("stod", "SignalToData", config={"ports": {"pass_value.x": "RELEASED"}})
    g.add_node("sink", "Sink")
    g.wire("src", "tick", "buf", "put.item")
    g.wire("buf", "flush", "join", "join.a")
    g.wire("const", "tick", "join", "join.b")
    g.wire("join", "join", "split", "fan.value")
    g.wire("split", "fan.out1", "latch", "release.data")
    g.wire("split", "fan.out2", "probe", "observe.value")
    g.wire("latch", "release", "dts", "convert.data")
    g.wire("dts", "convert", "stod", "pass_value.gate", slot=SLOT_SIGNAL)
    g.wire("dts", "convert", "stod", "pass_value.pass", slot=SLOT_TRIGGER)
    g.wire("stod", "pass_value", "sink", "consume.value")
    return g


def main() -> None:
    g = build()
    ensure_valid(g, PRIMITIVES)  # 连线合法性校验
    result = GraphInstance.build(g, PRIMITIVES)
    if not result.ok:
        raise RuntimeError(result.errors)
    world = result.instance

    def show(note: str) -> None:
        run = world.run_no
        print(render_epoch(world.timeline, run))
        print()
        print(note)

    # 宿主显式注入节拍；空 epoch 不会播种任何节点。
    world.run([Injection("src", "tick.trigger", SLOT_TRIGGER, Kind.SIGNAL, True), Injection("const", "tick.trigger", SLOT_TRIGGER, Kind.SIGNAL, True)])
    show("epoch 1: src→buf 累积, const→join.join.b；join 等待 a (未 flush)")
    world.run([Injection("src", "tick.trigger", SLOT_TRIGGER, Kind.SIGNAL, True), Injection("const", "tick.trigger", SLOT_TRIGGER, Kind.SIGNAL, True)])
    show("epoch 2: 同上，累积继续")

    # 注入 flush 触发：Buffer 取出全部累积 → join 同步 → split 扇出 → latch 缓存 / probe 记录
    world.run([Injection(node="buf", port="flush.trigger", slot=SLOT_TRIGGER, kind=Kind.SIGNAL, payload=True)])
    show("epoch 3: 注入 flush → join → split → latch 缓存数据(不输出) / probe 记录")

    # 注入 release 触发：Latch 释放缓存 → DataToSignal 算电平 → SignalToData 受控放行 → Sink
    world.run([Injection(node="latch", port="release.trigger", slot=SLOT_TRIGGER, kind=Kind.SIGNAL, payload=True)])
    show("epoch 4: 注入 release → latch 释放 → dts 电平 HIGH → stod 放行 → sink 吸收")

    print(render_event_archive(world.timeline))
    print()
    print(render_state(world.observable_state()))


if __name__ == "__main__":
    main()
