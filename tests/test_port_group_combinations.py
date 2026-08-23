"""端口组合行为 + 输入输出组行为:对照裁定文档的逐条设计验证。

验证依据:
- graph-node-protocol.md §2.4-2.5(Readiness / Signal 双语义)、§3.0(事件解释矩阵)、
  §5(内核边界:输出授权)、§6(Activation/Event 契约)
- graph-group-protocol.md 裁定 #1(组 = 调用契约)、#8(输出授权)、#10(handler 共享)、
  #11(空集语义)、#13(端口分区)、#14-15(Signal 归位)、#16(新事实要求)
- graph-concepts.md §九(NodeTurn 预算、反馈环跨轮迭代、消费保持)

与既有套件的分工:本文件只锁定**组合**行为(三端口同组交互、多组同节点交互),
单端口/单组的基础语义由 test_primitives / test_semantics_matrix / test_event_model 锁定。
"""

from eidolon_graph_ref.engine.event import Injection, Kind
from eidolon_graph_ref.engine.protocol import GroupOutput
from eidolon_graph_ref.engine.timeline import KIND_ERROR
from eidolon_graph_ref.model.graph import GraphDefinition, SLOT_DATA, SLOT_SIGNAL, SLOT_TRIGGER
from eidolon_graph_ref.model.node_type import Group, NodeType
from eidolon_graph_ref.model.ports import APPEND, DataIn, DataOut, SignalIn, TriggerIn
from eidolon_graph_ref.model.readiness import ALL, ANY, DATA, TRIGGER
from eidolon_primitives import PRIMITIVES

from conftest import errors, fired, make_world, node_state


def _produced(world, node="n"):
    return [e.payload for e in world.timeline.events.values() if e.producer == node]


# ==================================================================== 1. Data Event → TriggerIn = 载荷 + 激活
def test_data_event_into_trigger_carries_payload_and_activation():
    """Data Event 投递 TriggerIn:既激活组,载荷又并入 handler 实参(§3.0 触发载荷并入)。"""
    seen = []

    def handler(ctx):
        seen.append(ctx.data_in.get("go"))
        return GroupOutput(data_out={"out": ctx.data_in.get("go")})

    t = NodeType(
        name="TrigData",
        data_out=(DataOut("out"),),
        trigger_in=(TriggerIn("go"),),
        groups=(Group("go", triggers=("go",), outputs=("out",), handler=handler),),
    )
    g = GraphDefinition()
    g.add_node("n", "TrigData")
    world = make_world(g, {"TrigData": t})
    world.run([Injection("n", "go", SLOT_TRIGGER, Kind.DATA, "P1")])
    assert seen == ["P1"]  # 载荷可用
    assert _produced(world) == ["P1"]
    assert errors(world) == []


# ==================================================================== 2. Signal Event → TriggerIn = 纯激活
def test_signal_event_into_trigger_is_pure_activation():
    """Signal Event 投递 TriggerIn:只激活,不带载荷(has_payload 不成立,实参缺席)。"""
    seen = []

    def handler(ctx):
        seen.append("go" in ctx.data_in)  # 纯激活:无载荷键
        return GroupOutput()

    t = NodeType(
        name="TrigSignal",
        trigger_in=(TriggerIn("go"),),
        groups=(Group("go", triggers=("go",), handler=handler),),
    )
    g = GraphDefinition()
    g.add_node("n", "TrigSignal")
    world = make_world(g, {"TrigSignal": t})
    world.run([Injection("n", "go", SLOT_TRIGGER, Kind.SIGNAL, True)])
    assert seen == [False]  # 激活发生,但无载荷并入
    assert world.observable_state()["n"]["trigger_in"]["go"]["payload"] is None
    assert errors(world) == []


# ==================================================================== 3-4. 门控源选择:LOW 回默认 / HIGH 必须等待 / 翻转重估
def _gated_world():
    def handler(ctx):
        return GroupOutput(data_out={"out": ctx.data_in["x"]})

    t = NodeType(
        name="Gated",
        data_in=(DataIn("x", default="D", signal="gate"),),
        data_out=(DataOut("out"),),
        trigger_in=(TriggerIn("go"),),
        signal_in=(SignalIn("gate"),),
        groups=(Group("go", inputs=("x",), triggers=("go",), outputs=("out",), handler=handler),),
    )
    g = GraphDefinition()
    g.add_node("dts", "DataToSignal", config={"groups": {"convert": {"mode": "truthy"}}})
    g.add_node("n", "Gated")
    g.wire("dts", "convert", "n", "gate", slot=SLOT_SIGNAL)
    return make_world(g, {**PRIMITIVES, "Gated": t})


def test_gate_low_emits_static_default_not_cached_value():
    """源选择(裁定 #15):HIGH 期间缓存的动态值,LOW 期间触发 → 输出静态默认而非缓存。

    信号禁用的是"从动态源取值"的资格,不是接收/缓存能力——缓存保留但不参与解析。
    """
    world = _gated_world()
    world.run([
        Injection("dts", "convert.data", SLOT_DATA, Kind.DATA, 1),  # gate HIGH
        Injection("n", "x", SLOT_DATA, Kind.DATA, "cached"),
        Injection("n", "go", SLOT_TRIGGER, Kind.SIGNAL, True),
    ])
    assert _produced(world) == ["cached"]  # HIGH:动态缓存放行
    world.run([
        Injection("dts", "convert.data", SLOT_DATA, Kind.DATA, 0),  # gate LOW
        Injection("n", "go", SLOT_TRIGGER, Kind.SIGNAL, True),
    ])
    assert _produced(world) == ["cached", "D"]  # LOW:静态默认有效,缓存不参与
    assert errors(world) == []


def test_gate_high_requires_fresh_data():
    """翻转回 HIGH 后:DATA 叶要求 pending(必须等待新事实),仅有触发不执行。"""
    world = _gated_world()
    world.run([
        Injection("dts", "convert.data", SLOT_DATA, Kind.DATA, 1),
        Injection("n", "x", SLOT_DATA, Kind.DATA, "cached"),
        Injection("n", "go", SLOT_TRIGGER, Kind.SIGNAL, True),
    ])
    world.run([
        Injection("dts", "convert.data", SLOT_DATA, Kind.DATA, 0),  # LOW
        Injection("n", "go", SLOT_TRIGGER, Kind.SIGNAL, True),
    ])
    world.run([
        Injection("dts", "convert.data", SLOT_DATA, Kind.DATA, 1),  # 回 HIGH
        Injection("n", "go", SLOT_TRIGGER, Kind.SIGNAL, True),  # 只给触发,不给数据
    ])
    assert _produced(world) == ["cached", "D"]  # 第三次不执行:动态模式必须等待
    assert errors(world) == []


# ==================================================================== 5. 未绑定 SignalIn = 纯数据输入(语义二)
def test_unbound_signal_in_behaves_as_plain_data_input():
    """未绑定信号按数据聚合(§2.5 语义二):pending 触发 DATA 叶,handler 读 level,fire 时消费。"""
    seen = []

    def handler(ctx):
        seen.append(ctx.data_in["s"])
        return GroupOutput(data_out={"out": ctx.data_in["s"]})

    t = NodeType(
        name="SigIn",
        data_out=(DataOut("out"),),
        signal_in=(SignalIn("s"),),
        groups=(Group("g", inputs=("s",), outputs=("out",), handler=handler),),
    )
    g = GraphDefinition()
    g.add_node("n", "SigIn")
    world = make_world(g, {"SigIn": t})
    world.run([Injection("n", "s", SLOT_SIGNAL, Kind.SIGNAL, True)])
    world.run([Injection("n", "s", SLOT_SIGNAL, Kind.SIGNAL, False)])
    assert seen == [True, False]  # handler 读到的是电平
    assert _produced(world) == [True, False]
    injected = [e for e in world.timeline.events.values() if e.producer is None]
    assert all(e.status == "consumed" for e in injected)  # 与数据同构:fire 时消费
    assert errors(world) == []


# ==================================================================== 6. 多组同 epoch:组间经节点状态传递(裁定 #1/#13)
def test_groups_handoff_via_node_state_within_one_epoch():
    """同节点多组:按声明序独立执行;ga 提交的状态,gb 同 epoch 可见(状态是唯一组间通道)。"""
    def put(ctx):
        return GroupOutput(state={"seen": ctx.data_in["a"]})

    def take(ctx):
        return GroupOutput(data_out={"out": ctx.state["seen"]})

    t = NodeType(
        name="TwoGroups",
        data_in=(DataIn("a"),),
        data_out=(DataOut("out"),),
        trigger_in=(TriggerIn("go"),),
        state_defaults={"seen": None},
        groups=(
            Group("ga", inputs=("a",), handler=put),
            Group("gb", triggers=("go",), outputs=("out",), handler=take),
        ),
    )
    g = GraphDefinition()
    g.add_node("n", "TwoGroups")
    world = make_world(g, {"TwoGroups": t})
    world.run([
        Injection("n", "a", SLOT_DATA, Kind.DATA, "v1"),
        Injection("n", "go", SLOT_TRIGGER, Kind.SIGNAL, True),
    ])
    assert fired(world, 1) == [("n", "ga"), ("n", "gb")]  # 声明序,各自独立预算
    assert _produced(world) == ["v1"]  # gb 读到 ga 同 epoch 提交的状态
    assert errors(world) == []


# ==================================================================== 7. 输出授权(§5:写必须属于本组)
def test_unauthorized_output_is_dropped_with_kind_error():
    """handler 写 group.outputs 之外:KIND_ERROR + 丢弃该输出;授权输出与未声明端口分别处理。"""
    def handler(ctx):
        return GroupOutput(data_out={"o1": "ok", "o2": "unauthorized", "ghost": "undeclared"})

    t = NodeType(
        name="Auth",
        data_in=(DataIn("a"),),
        data_out=(DataOut("o1"), DataOut("o2")),
        groups=(Group("g", inputs=("a",), outputs=("o1",), handler=handler),),  # 只授权 o1
    )
    g = GraphDefinition()
    g.add_node("n", "Auth")
    world = make_world(g, {"Auth": t})
    world.run([Injection("n", "a", SLOT_DATA, Kind.DATA, 1)])
    assert _produced(world) == ["ok"]  # 只有授权输出成为事件
    msgs = [e.message for e in world.timeline.entries if e.kind == KIND_ERROR]
    assert len(msgs) == 2 and all("not authorized" in m for m in msgs)  # o2 与 ghost 各一条


# ==================================================================== 8. NodeTurn 预算:反馈环跨轮迭代(§九)
def test_feedback_loop_iterates_across_epochs_not_within():
    """自环:每 epoch 至多一次 fire(预算);静止需宿主唤醒;run([]) 立即静止(裁定 #6)。"""
    t = NodeType(
        name="Loop",
        data_in=(DataIn("i"),),
        data_out=(DataOut("o"),),
        groups=(Group("g", inputs=("i",), outputs=("o",), handler=lambda ctx: GroupOutput(data_out={"o": ctx.data_in["i"] + 1})),),
    )
    g = GraphDefinition()
    g.add_node("n", "Loop")
    g.wire("n", "o", "n", "i")  # 自环
    world = make_world(g, {"Loop": t})
    world.run([Injection("n", "i", SLOT_DATA, Kind.DATA, 0)])
    assert len(fired(world, 1)) == 1  # 环回的数据同 epoch 不再执行(预算)
    assert _produced(world) == [1]
    world.run([])  # 无播种:立即静止,环回 pending 不自动处理
    assert len(fired(world, 2)) == 0
    world.run([Injection("n", "i", SLOT_DATA, Kind.DATA, 100)])  # 宿主唤醒 → 跨轮迭代继续
    assert len(fired(world, 3)) == 1
    assert _produced(world) == [1, 101]  # REPLACE 缓存:最新值胜出,两轮 pending 一并消费
    assert errors(world) == []


# ==================================================================== 9. 显式 readiness:ANY / 嵌套(裁定 #11)
def test_explicit_any_readiness_fires_on_partial_inputs():
    """ANY(DATA a, DATA b):单输入 pending 即执行,缺席输入回退默认(旧 ON_ANY_DATA 的谓词表达)。"""
    t = NodeType(
        name="AnyJoin",
        data_in=(DataIn("a", default=0), DataIn("b", default=0)),
        data_out=(DataOut("out"),),
        groups=(
            Group("g", inputs=("a", "b"), outputs=("out",), readiness=ANY(DATA("a"), DATA("b")),
                  handler=lambda ctx: GroupOutput(data_out={"out": (ctx.data_in["a"], ctx.data_in["b"])})),
        ),
    )
    g = GraphDefinition()
    g.add_node("n", "AnyJoin")
    world = make_world(g, {"AnyJoin": t})
    world.run([Injection("n", "a", SLOT_DATA, Kind.DATA, 1)])  # 只有 a
    assert _produced(world) == [(1, 0)]  # b 静态回退默认
    assert errors(world) == []


def test_nested_readiness_all_of_any_and_trigger():
    """嵌套 ALL(ANY(DATA,DATA), TRIGGER):数据任一 + 触发门(旧 ON_DATA_AND_TRIGGER 的谓词表达)。"""
    t = NodeType(
        name="Nested",
        data_in=(DataIn("a"), DataIn("b")),
        data_out=(DataOut("out"),),
        trigger_in=(TriggerIn("go"),),
        groups=(
            Group("g", inputs=("a", "b"), triggers=("go",), outputs=("out",),
                  readiness=ALL(ANY(DATA("a"), DATA("b")), TRIGGER("go")),
                  handler=lambda ctx: GroupOutput(data_out={"out": "fired"})),
        ),
    )
    g = GraphDefinition()
    g.add_node("n", "Nested")
    world = make_world(g, {"Nested": t})
    world.run([Injection("n", "a", SLOT_DATA, Kind.DATA, 1)])  # 数据齐但无触发门
    assert _produced(world) == []
    world.run([Injection("n", "go", SLOT_TRIGGER, Kind.SIGNAL, True)])  # 触发门到达
    assert _produced(world) == ["fired"]
    assert errors(world) == []


# ==================================================================== 10. APPEND 缓存:消费后值保持,继续累积(§九 消费保持)
def test_append_cache_retains_value_after_consumption():
    """APPEND 端口:fire 消费 pending 后 value 保持;后续事件在保留值上继续累积。"""
    t = NodeType(
        name="Acc",
        data_in=(DataIn("items", cache=APPEND),),
        data_out=(DataOut("out"),),
        groups=(Group("g", inputs=("items",), outputs=("out",), handler=lambda ctx: GroupOutput(data_out={"out": list(ctx.data_in["items"])})),),
    )
    g = GraphDefinition()
    g.add_node("n", "Acc")
    world = make_world(g, {"Acc": t})
    world.run([Injection("n", "items", SLOT_DATA, Kind.DATA, 1)])
    world.run([Injection("n", "items", SLOT_DATA, Kind.DATA, 2)])
    assert _produced(world) == [[1], [1, 2]]  # 第二次读到的累积含第一轮已消费的值
    assert errors(world) == []
