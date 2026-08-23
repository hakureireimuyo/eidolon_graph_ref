"""DSL v2 prototype: function signatures as group contracts, on the unmodified kernel.

Scope pinned 2026-08-22 — prove three core semantics compile to the same
NodeType IR and execute correctly: Add (data inputs + fallback defaults),
Counter (this + State + Trigger), Gate (Signal dependency + Gated binding).
Plus the two group-scoping and error-behavior cases the baseline implies.
"""
import pytest

from eidolon_dsl import Asset, Config, Gated, NodeDefinition, Signal, State, Trigger, group
from eidolon_graph_ref.engine import GraphInstance, Injection, Kind
from eidolon_graph_ref.engine.timeline import KIND_ERROR
from eidolon_graph_ref.model import (
    DefinitionError,
    GraphDefinition,
    NodeType,
    SLOT_DATA,
    SLOT_SIGNAL,
    SLOT_TRIGGER,
)
from eidolon_primitives import PRIMITIVES


class Add(NodeDefinition):
    @group
    def add(a: int = 0, b: int = 0) -> int:
        return a + b


class AddSub(NodeDefinition):
    @group
    def add(a: int = 0, b: int = 0) -> int:
        return a + b

    @group
    def sub(a: int = 0, b: int = 0) -> int:
        return a - b


class Counter(NodeDefinition):
    count: State[int] = 0

    @group
    def tick(this, trigger: Trigger, step: int = 1) -> int:
        this.count += step
        return this.count


class Gate(NodeDefinition):
    @group
    def release(gate: Signal, value: Gated[int, "gate"] = 7) -> int:
        return value


class BufferDs(NodeDefinition):
    items: State[list] = []

    @group
    def put(this, item: int) -> None:
        this.items = [*this.items, item]

    @group
    def flush(this, trigger: Trigger) -> list:
        return this.items


# ---- helpers -----------------------------------------------------------------


def _build(node_type: NodeType):
    graph = GraphDefinition()
    graph.add_node("n", node_type.name)
    report = GraphInstance.build(graph, {node_type.name: node_type})
    assert report.ok, report.errors
    return report.instance


def _produced(world, node: str = "n"):
    return [e.payload for e in world.timeline.events.values() if e.producer == node]


def _errors(world):
    return [e.message for e in world.timeline.entries if e.kind == KIND_ERROR]


def _state(world, node: str = "n"):
    return world.observable_state()[node]["state"]


# ---- IR isomorphism -----------------------------------------------------------


def test_add_compiles_to_node_type_ir():
    assert isinstance(Add.TYPE, NodeType)
    assert Add.TYPE.name == "Add"
    g = Add.TYPE.group("add")
    assert g.inputs == ("add.a", "add.b")
    assert g.outputs == ("add",)
    assert g.readiness is None  # default semantics: ALL data inputs
    assert [p.name for p in Add.TYPE.data_in] == ["add.a", "add.b"]
    assert [p.default for p in Add.TYPE.data_in] == [0, 0]
    assert [p.name for p in Add.TYPE.data_out] == ["add"]


def test_same_parameter_name_in_two_groups_creates_distinct_ports():
    # ports belong to groups: add.a/sub.a are four distinct qualified ports
    assert [p.name for p in AddSub.TYPE.data_in] == ["add.a", "add.b", "sub.a", "sub.b"]
    world = _build(AddSub.TYPE)
    world.run([Injection("n", "add.a", SLOT_DATA, Kind.DATA, 10)])
    # 裁定 16:无触发器组要求新事实——sub 无 pending 输入,不连带触发
    assert _produced(world) == [10]
    world.run([Injection("n", "sub.a", SLOT_DATA, Kind.DATA, 5)])
    assert _produced(world) == [10, 5]  # add no longer ready: add.a is dynamic, not pending
    assert _errors(world) == []


# ---- Add: data inputs + fallback defaults --------------------------------------


def test_add_runs_with_fallback_defaults():
    world = _build(Add.TYPE)
    world.run([Injection("n", "add.a", SLOT_DATA, Kind.DATA, 2)])
    assert _produced(world) == [2]  # b absent → fallback 0
    world.run([
        Injection("n", "add.a", SLOT_DATA, Kind.DATA, 10),
        Injection("n", "add.b", SLOT_DATA, Kind.DATA, 5),
    ])
    assert _produced(world) == [2, 15]
    assert _errors(world) == []


# ---- Counter: this + State + Trigger --------------------------------------------


def test_counter_state_and_trigger():
    world = _build(Counter.TYPE)
    world.run([Injection("n", "tick.trigger", SLOT_TRIGGER, Kind.SIGNAL, True)])
    assert _produced(world) == [1]
    world.run([Injection("n", "tick.trigger", SLOT_TRIGGER, Kind.SIGNAL, True)])
    assert _produced(world) == [1, 2]
    assert _state(world)["count"] == 2
    assert _errors(world) == []


def test_counter_config_override_uses_port_config():
    graph = GraphDefinition()
    graph.add_node("n", "Counter", config={"ports": {"tick.step": 5}})
    report = GraphInstance.build(graph, {"Counter": Counter.TYPE})
    assert report.ok, report.errors
    world = report.instance
    world.run([Injection("n", "tick.trigger", SLOT_TRIGGER, Kind.SIGNAL, True)])
    assert _produced(world) == [5]


# ---- Gate: Signal dependency + Gated binding ------------------------------------


def test_gate_unwired_direct_injection():
    world = _build(Gate.TYPE)
    world.run([Injection("n", "release.value", SLOT_DATA, Kind.DATA, 42)])
    assert _produced(world) == [42]  # unwired gate → always active → dynamic value passes
    assert _errors(world) == []


def test_gate_wired_signal_selects_dynamic_vs_fallback():
    graph = GraphDefinition()
    graph.add_node("dts", "DataToSignal")
    graph.add_node("n", "Gate")
    graph.add_node("sink", "Sink")
    graph.wire("dts", "convert", "n", "release.gate", slot=SLOT_SIGNAL)
    graph.wire("n", "release", "sink", "consume.value")
    report = GraphInstance.build(
        graph, {"Gate": Gate.TYPE, "DataToSignal": PRIMITIVES["DataToSignal"], "Sink": PRIMITIVES["Sink"]}
    )
    assert report.ok, report.errors
    world = report.instance

    world.run([Injection("dts", "convert.data", SLOT_DATA, Kind.DATA, 0)])  # gate → LOW
    # 裁定 16:仅有门控信号、无数据新事实 → 不触发
    assert _state(world, "sink")["last"] is None

    world.run([Injection("n", "release.value", SLOT_DATA, Kind.DATA, 42)])  # 数据到达,仍 LOW → fallback
    assert _state(world, "sink")["last"] == 7

    # sequential epochs: the gate level must reach the node BEFORE the data
    # event (in one batch, injections are delivered before upstream fires)
    world.run([Injection("dts", "convert.data", SLOT_DATA, Kind.DATA, 1)])  # gate → HIGH (level persists)
    world.run([Injection("n", "release.value", SLOT_DATA, Kind.DATA, 42)])
    assert _state(world, "sink")["last"] == 42  # dynamic mode → real value passes
    assert _errors(world) == []


# ---- multi-group, -> None, whole-value state --------------------------------------


def test_buffer_multi_group_and_no_output():
    world = _build(BufferDs.TYPE)
    world.run([Injection("n", "put.item", SLOT_DATA, Kind.DATA, 1)])
    world.run([Injection("n", "put.item", SLOT_DATA, Kind.DATA, 2)])
    assert _produced(world) == []  # put groups produce no output event
    world.run([Injection("n", "flush.trigger", SLOT_TRIGGER, Kind.SIGNAL, True)])
    assert _produced(world) == [[1, 2]]
    assert _errors(world) == []


def test_in_place_state_mutation_takes_effect():
    """this 视图全量写回(裁定 2026-08-23):原地变异直接生效,跨 fire 累积。"""
    class Mut(NodeDefinition):
        items: State[list] = []

        @group
        def run(this, x: int) -> None:
            this.items.append(x)  # 原地变异:fire 结束整个工作副本写回

    world = _build(Mut.TYPE)
    world.run([Injection("n", "run.x", SLOT_DATA, Kind.DATA, 1)])
    world.run([Injection("n", "run.x", SLOT_DATA, Kind.DATA, 2)])
    assert _state(world)["items"] == [1, 2]
    assert _errors(world) == []

def test_state_to_data_ownership_boundary():
    """State→Data 是 ownership 边界(裁定 2026-08-23):输出 state 持有对象时
    输出侧复制——变异事件载荷不影响 state;Data Plane 内部仍零拷贝。"""
    class Emit(NodeDefinition):
        items: State[list] = []

        @group
        def load(this, x: list) -> None:
            this.items = x

        @group
        def emit(this, trigger: Trigger) -> list:
            return this.items  # state 持有对象直接输出 → 边界复制

    world = _build(Emit.TYPE)
    world.run([Injection("n", "load.x", SLOT_DATA, Kind.DATA, [1, 2])])
    world.run([Injection("n", "emit.trigger", SLOT_TRIGGER, Kind.SIGNAL, True)])
    (payload,) = _produced(world)
    payload.append(99)  # 变异事件载荷
    assert _state(world)["items"] == [1, 2]  # state 不受影响
    assert _errors(world) == []


# ---- compile-time and runtime error behavior --------------------------------------


def test_self_receiver_is_rejected():
    with pytest.raises(DefinitionError, match="this"):
        class Bad(NodeDefinition):
            @group
            def run(self, x: int) -> int:
                return x


def test_parameter_order_special_before_data():
    with pytest.raises(DefinitionError, match="precede data inputs"):
        class Bad(NodeDefinition):
            @group
            def run(this, x: int, trigger: Trigger) -> int:
                return x


def test_undeclared_state_write_records_kind_error():
    class BadState(NodeDefinition):
        @group
        def run(this, x: int) -> int:
            this.nope = x
            return x

    world = _build(BadState.TYPE)
    world.run([Injection("n", "run.x", SLOT_DATA, Kind.DATA, 1)])
    assert _produced(world) == []  # handler raised → no output
    assert any("undeclared state field" in m for m in _errors(world))


def test_definition_classes_are_compile_time_only():
    with pytest.raises(TypeError, match="compile-time"):
        Add()


# ---- Asset: declaration collected to node level, value injected into the body ----


def test_asset_parameter_receives_resolved_capability():
    from fake_assets import DatabaseCapability, FakeAssetSystem

    class DbQuery(NodeDefinition):
        @group
        def query(this, db: Asset[DatabaseCapability], sql: str) -> list:
            return db.query(sql)

    assert [a.name for a in DbQuery.TYPE.asset_in] == ["db"]  # stripped to node level
    assert DbQuery.TYPE.group("query").inputs == ("query.sql",)  # not a group input

    system = FakeAssetSystem()
    ref = system.create_db("postgres://x")
    graph = GraphDefinition()
    graph.add_node("n", "DbQuery")
    graph.bind_asset(node_id="n", slot="db", asset_id=ref.asset_id)
    report = GraphInstance.build(graph, {"DbQuery": DbQuery.TYPE}, asset_resolver=system)
    assert report.ok, report.errors
    world = report.instance
    world.run([Injection("n", "query.sql", SLOT_DATA, Kind.DATA, "SELECT 1")])
    assert _produced(world) == [["db-1:SELECT 1"]]
    assert _errors(world) == []

# ---- 裁定 12:signals= + dict 返回协议(2026-08-23) ------------------------------


class ThreshDs(NodeDefinition):
    @group(outputs=("over", "under"), defaults={"limit": 5})
    def check(cfg: Config, value: int) -> dict:
        if value >= cfg["limit"]:
            return {"over": value}
        return {"under": value}


class CmpDs(NodeDefinition):
    @group(outputs=("gt", "eq"), signals=("a_gt_b",))
    def compare(a, b) -> dict:
        gt = a is not None and b is not None and a > b
        return {"gt": gt, "eq": a is not None and b is not None and a == b, "a_gt_b": gt}


def _port_events(world, port: str, node: str = "n"):
    return [e for e in world.timeline.events.values() if e.producer == node and e.port == port]


def test_signals_compiles_to_three_tables():
    assert [p.name for p in CmpDs.TYPE.data_out] == ["compare.gt", "compare.eq"]
    assert [p.name for p in CmpDs.TYPE.signal_out] == ["compare.a_gt_b"]
    assert CmpDs.TYPE.group("compare").outputs == ("compare.gt", "compare.eq", "compare.a_gt_b")


def test_dict_protocol_branches_emit_only_one_side():
    world = _build(ThreshDs.TYPE)
    world.run([Injection("n", "check.value", SLOT_DATA, Kind.DATA, 7)])
    world.run([Injection("n", "check.value", SLOT_DATA, Kind.DATA, 2)])
    assert [e.payload for e in _port_events(world, "check.over")] == [7]
    assert [e.payload for e in _port_events(world, "check.under")] == [2]
    assert _errors(world) == []


def test_dict_protocol_none_value_is_legal_payload():
    class NoneEmits(NodeDefinition):
        @group(outputs=("a", "b"))
        def run(x: int) -> dict:
            return {"a": None}  # None = 合法载荷照发;b 缺失 = 无事件

    world = _build(NoneEmits.TYPE)
    world.run([Injection("n", "run.x", SLOT_DATA, Kind.DATA, 1)])
    a_events = _port_events(world, "run.a")
    assert len(a_events) == 1 and a_events[0].payload is None
    assert _port_events(world, "run.b") == []
    assert _errors(world) == []


def test_dict_protocol_mixed_data_and_signal_outputs():
    world = _build(CmpDs.TYPE)
    world.run([Injection("n", "compare.a", SLOT_DATA, Kind.DATA, 5),
               Injection("n", "compare.b", SLOT_DATA, Kind.DATA, 3)])
    assert [e.payload for e in _port_events(world, "compare.gt")] == [True]
    assert [e.payload for e in _port_events(world, "compare.eq")] == [False]
    assert [e.payload for e in _port_events(world, "compare.a_gt_b")] == [True]
    assert _errors(world) == []


def test_dict_protocol_unknown_key_records_kind_error():
    class BadKey(NodeDefinition):
        @group(outputs=("a", "b"))
        def run(x: int) -> dict:
            return {"nope": x}

    world = _build(BadKey.TYPE)
    world.run([Injection("n", "run.x", SLOT_DATA, Kind.DATA, 1)])
    assert _produced(world) == []
    assert any("undeclared output name" in m for m in _errors(world))


def test_dict_protocol_non_dict_return_records_kind_error():
    class BadRet(NodeDefinition):
        @group(outputs=("a", "b"))
        def run(x: int) -> tuple:
            return (x, x)

    world = _build(BadRet.TYPE)
    world.run([Injection("n", "run.x", SLOT_DATA, Kind.DATA, 1)])
    assert _produced(world) == []
    assert any("must return a dict" in m for m in _errors(world))


def test_zero_output_group_returning_payload_records_kind_error():
    class BadNone(NodeDefinition):
        @group
        def run(x: int) -> None:
            return x  # -> None 组返回载荷:违规产出

    world = _build(BadNone.TYPE)
    world.run([Injection("n", "run.x", SLOT_DATA, Kind.DATA, 1)])
    assert _produced(world) == []
    assert any("declares no outputs" in m for m in _errors(world))


def test_signals_requires_outputs():
    with pytest.raises(DefinitionError, match="signals= requires outputs="):
        class Bad(NodeDefinition):
            @group(signals=("s",))
            def run(x: int) -> dict:
                return {"s": True}


def test_doc_and_tags_readonly_declaration_functions():
    """tags/doc 只读声明函数(裁定 2026-08-23):@staticmethod 显式重载,编译期求值一次。"""
    from eidolon_graph_ref.model.node_type import DocSection, DocSpec

    class Documented(NodeDefinition):
        @group
        def run(x: int) -> int:
            return x

        @staticmethod
        def tags() -> tuple[str, ...]:
            return ("category:test",)

        @staticmethod
        def doc() -> DocSpec:
            return DocSpec("摘要", sections=(DocSection("行为", lines=("第一行",)),))

    assert Documented.TYPE.doc.summary == "摘要"
    assert Documented.TYPE.doc.sections[0].lines == ("第一行",)
    assert Documented.TYPE.tags == ("category:test",)


def test_tags_doc_default_to_empty_when_not_overridden():
    """未重载声明函数的节点:tags = ()、doc = None(基类默认,compile_dsl 脚本节点同路径)。"""
    assert Add.TYPE.tags == ()
    assert Add.TYPE.doc is None


def test_tags_declared_as_attribute_is_rejected():
    with pytest.raises(DefinitionError, match="read-only @staticmethod"):
        class Bad(NodeDefinition):
            tags = ("category:x",)

            @group
            def run(x: int) -> int:
                return x


def test_doc_declared_as_attribute_is_rejected():
    from eidolon_graph_ref.model.node_type import DocSpec

    with pytest.raises(DefinitionError, match="read-only @staticmethod"):
        class Bad(NodeDefinition):
            doc = DocSpec("摘要")

            @group
            def run(x: int) -> int:
                return x
