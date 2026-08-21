"""节点协议 ABI 契约测试:内核 ↔ 外部节点(graph-node-protocol.md)。

全部节点为**完全外部定义**——直接构造 NodeType,不经任何内置包工厂
(eidolon_primitives._define);外部实现者可见面只有:model 公开声明类型 +
engine/protocol.py 的 TickContext/TickOutput/InitContext + GraphInstance.build。

每个用例 docstring 引用 graph-node-protocol.md 的 §;与 test_plane_boundaries.py
锁定的边界(TickContext 字段白名单等)不重复,引用即可。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eidolon_graph_ref.engine.event import Injection, Kind
from eidolon_graph_ref.engine.instance import GraphInstance
from eidolon_graph_ref.engine.protocol import InitContext, TickContext, TickOutput
from eidolon_graph_ref.engine.timeline import KIND_ERROR
from eidolon_graph_ref.model.assets import AssetIn
from eidolon_graph_ref.model.graph import GraphDefinition, SLOT_DATA, SLOT_TRIGGER
from eidolon_graph_ref.model.node_type import InputGroup, NodeType, Policy
from eidolon_graph_ref.model.ports import DataIn, DataOut, TriggerIn
from eidolon_primitives import PRIMITIVES

from conftest import data_port, deliveries, errors, fired, make_world, node_state, quiesces
from fake_assets import DatabaseCapability, FakeAssetSystem, LockedDatabase


# ==================================================================== 声明 → 运行全链路
def test_external_node_declaration_wires_and_runs():
    """外部节点声明 → 连线 → 构建 → 执行 → 输出投递 + 状态提交全链路(§2,3,5)。"""

    def tick(ctx):
        return TickOutput(data_out={"out": ctx.data_in["in"] * 2}, state={"seen": ctx.state["seen"] + 1})

    ext = NodeType(
        name="ExternalDouble",
        data_in=(DataIn("in"),),
        data_out=(DataOut("out"),),
        state_defaults={"seen": 0},
        groups=(InputGroup("go", inputs=("in",), policy=Policy.ON_ANY_DATA),),
        tick=tick,
    )
    g = GraphDefinition("external")
    g.add_node("ext", "ExternalDouble")
    g.add_node("sink", "Sink")
    g.wire("ext", "out", "sink", "in")
    world = make_world(g, {**PRIMITIVES, "ExternalDouble": ext})
    world.run([Injection("ext", "in", SLOT_DATA, Kind.DATA, 21)])
    assert deliveries(world, "ext", "out") == [42]  # 产出即投递
    assert node_state(world, "sink")["last"] == 42  # 下游吸收
    assert node_state(world, "ext")["seen"] == 1  # 状态提交
    assert ("ext", "go") in fired(world, 1)


# ==================================================================== 四种 Readiness 策略
def test_each_policy_activates():
    """四种 Readiness 策略:正确 pending 集激活一次且仅一次(§2,3)。"""

    def counter_tick(ctx):
        return TickOutput(state={"hits": ctx.state["hits"] + 1})

    def define(policy, data_names, trigger_names):
        return NodeType(
            name=f"Policy_{policy.value}",
            data_in=tuple(DataIn(n) for n in data_names),
            trigger_in=tuple(TriggerIn(n) for n in trigger_names),
            state_defaults={"hits": 0},
            groups=(
                InputGroup("g", inputs=data_names, triggers=trigger_names, policy=policy),
            ),
            tick=counter_tick,
        )

    # ON_ALL_DATA_READY:动态端口不全 ready 不执行,齐备执行一次。
    # 经上游转发节点连线,a/b 自构建起即动态端口(静态端口不参与触发)。
    def passthrough_tick(ctx):
        return TickOutput(data_out={"out": ctx.data_in["in"]})

    passthrough = NodeType(
        name="Pass",
        data_in=(DataIn("in"),),
        data_out=(DataOut("out"),),
        groups=(InputGroup("go", inputs=("in",), policy=Policy.ON_ANY_DATA),),
        tick=passthrough_tick,
    )
    t = define(Policy.ON_ALL_DATA_READY, ("a", "b"), ())
    g = GraphDefinition("p_all")
    g.add_node("u1", "Pass")
    g.add_node("u2", "Pass")
    g.add_node("n", "Policy_on_all_data_ready")
    g.wire("u1", "out", "n", "a")
    g.wire("u2", "out", "n", "b")
    w = make_world(g, {**PRIMITIVES, "Policy_on_all_data_ready": t, "Pass": passthrough})
    w.run([Injection("u1", "in", SLOT_DATA, Kind.DATA, 1)])  # 只有 a 到达
    assert ("n", "g") not in fired(w, 1)  # b 未 ready → 不执行
    w.run([Injection("u2", "in", SLOT_DATA, Kind.DATA, 2)])
    assert len([f for f in fired(w, 2) if f == ("n", "g")]) == 1
    assert node_state(w, "n")["hits"] == 1

    # ON_ANY_DATA:任一数据即执行
    t = define(Policy.ON_ANY_DATA, ("a", "b"), ())
    g = GraphDefinition("p_any")
    g.add_node("n", "Policy_on_any_data")
    w = make_world(g, {**PRIMITIVES, "Policy_on_any_data": t})
    w.run([Injection("n", "a", SLOT_DATA, Kind.DATA, 1)])
    assert len([f for f in fired(w, 1) if f == ("n", "g")]) == 1
    assert node_state(w, "n")["hits"] == 1

    # ON_TRIGGER:纯事件节点(数据条件真空为真)
    t = define(Policy.ON_TRIGGER, (), ("go",))
    g = GraphDefinition("p_trig")
    g.add_node("n", "Policy_on_trigger")
    w = make_world(g, {**PRIMITIVES, "Policy_on_trigger": t})
    w.run([Injection("n", "go", SLOT_TRIGGER, Kind.SIGNAL, True)])
    assert len([f for f in fired(w, 1) if f == ("n", "g")]) == 1
    assert node_state(w, "n")["hits"] == 1

    # ON_DATA_AND_TRIGGER:数据齐 pending + 触发 pending(显式门控)
    t = define(Policy.ON_DATA_AND_TRIGGER, ("a",), ("go",))
    g = GraphDefinition("p_both")
    g.add_node("n", "Policy_on_data_and_trigger")
    w = make_world(g, {**PRIMITIVES, "Policy_on_data_and_trigger": t})
    w.run([Injection("n", "a", SLOT_DATA, Kind.DATA, 1)])
    assert ("n", "g") not in fired(w, 1)  # 有数据无触发 → 不执行
    w.run([Injection("n", "go", SLOT_TRIGGER, Kind.SIGNAL, True)])
    assert len([f for f in fired(w, 2) if f == ("n", "g")]) == 1
    assert node_state(w, "n")["hits"] == 1


# ==================================================================== 错误约定
def test_error_convention_raise_pending_retry_succeeds():
    """tick 异常 → KIND_ERROR、无输出、pending 保留;下一 epoch 重试成功(§3)。"""
    flaky = {"fail": True}

    def tick(ctx):
        if flaky["fail"]:
            flaky["fail"] = False
            raise RuntimeError("boom")
        return TickOutput(data_out={"out": ctx.data_in["in"]})

    flaky_type = NodeType(
        name="ExtFlaky",
        data_in=(DataIn("in"),),
        data_out=(DataOut("out"),),
        groups=(InputGroup("go", inputs=("in",), policy=Policy.ON_ANY_DATA),),
        tick=tick,
    )
    g = GraphDefinition("retry")
    g.add_node("bad", "ExtFlaky")
    g.add_node("sink", "Sink")
    g.wire("bad", "out", "sink", "in")
    world = make_world(g, {**PRIMITIVES, "ExtFlaky": flaky_type})
    world.run([Injection("bad", "in", SLOT_DATA, Kind.DATA, 1)])
    assert errors(world) == ["RuntimeError: boom"]  # KIND_ERROR
    assert deliveries(world, "bad", "out") == []  # 无输出
    assert data_port(world, "bad", "in")["pending"] is True  # pending 保留
    world.run([Injection("bad", "in", SLOT_DATA, Kind.DATA, 2)])  # 唤醒重试
    assert node_state(world, "sink")["last"] == 2  # 重试成功


# ==================================================================== 资产四概念
def test_asset_declare_bind_resolve_use():
    """资产四概念:AssetIn 声明 → bind_asset 绑定 → resolver 解析 → ctx.assets 使用(§4)。"""

    def tick(ctx):
        rows = ctx.assets["db"].query(ctx.data_in["sql"])
        return TickOutput(data_out={"rows": rows})

    query_type = NodeType(
        name="ExtQuery",
        data_in=(DataIn("sql"),),
        data_out=(DataOut("rows"),),
        asset_in=(AssetIn("db", DatabaseCapability),),
        groups=(InputGroup("q", inputs=("sql",), policy=Policy.ON_ANY_DATA),),
        tick=tick,
    )
    assets = FakeAssetSystem()
    ref = assets.create_db("sqlite://main")
    g = GraphDefinition("assets")
    g.add_node("q", "ExtQuery")
    g.add_node("sink", "Sink")
    g.wire("q", "rows", "sink", "in")
    g.bind_asset("q", "db", ref.asset_id)
    result = GraphInstance.build(g, {**PRIMITIVES, "ExtQuery": query_type}, asset_resolver=assets)
    assert result.ok, result.errors
    world = result.instance
    world.run([Injection("q", "sql", SLOT_DATA, Kind.DATA, "SELECT 1")])
    assert node_state(world, "sink")["last"] == [f"{ref.asset_id}:SELECT 1"]


# ==================================================================== 使用权非所有权
def test_node_has_use_right_not_ownership():
    """使用权非所有权:实例销毁不关闭能力;使用面不含管理操作(§10,graph-asset-protocols.md §13)。"""
    assets = FakeAssetSystem()
    ref = assets.create_db("sqlite://main")
    db = assets.instance(ref.asset_id)

    def tick(ctx):
        ctx.assets["db"].query("SELECT 1")
        return TickOutput()

    node_type = NodeType(
        name="ExtUser",
        data_in=(DataIn("go"),),
        asset_in=(AssetIn("db", DatabaseCapability),),
        groups=(InputGroup("g", inputs=("go",), policy=Policy.ON_ANY_DATA),),
        tick=tick,
    )
    g = GraphDefinition("ownership")
    g.add_node("u", "ExtUser")
    g.bind_asset("u", "db", ref.asset_id)
    result = GraphInstance.build(g, {**PRIMITIVES, "ExtUser": node_type}, asset_resolver=assets)
    assert result.ok, result.errors
    world = result.instance
    world.run([Injection("u", "go", SLOT_DATA, Kind.DATA, 1)])
    del world  # GraphInstance 销毁仅释放自身引用,不调用任何管理操作
    assert db.closed is False  # 所有权在资产系统
    assert db.failed is False
    # Capability 协议表面 = 使用面,不含管理操作(close/health 等)
    assert not hasattr(DatabaseCapability, "close")
    assert not hasattr(DatabaseCapability, "health")


# ==================================================================== init 钩子(§7)
def test_init_merges_delta_over_state_defaults():
    """init 增量合并于 state_defaults;未提及字段保持默认(§7)。"""

    def init(ctx):
        return {"count": ctx.config["start"]}

    def tick(ctx):
        return TickOutput(data_out={"out": ctx.state["count"]}, state={"count": ctx.state["count"] + 1})

    src = NodeType(
        name="InitCounter",
        data_out=(DataOut("out"),),
        state_defaults={"count": 0, "tag": "x"},
        config_defaults={"start": 0},
        tick=tick,
        init=init,
    )
    assert src.to_dict()["has_init"] is True  # 声明可观察形态
    g = GraphDefinition("init_merge")
    g.add_node("c", "InitCounter", start=7)
    g.add_node("sink", "Sink")
    g.wire("c", "out", "sink", "in")
    world = make_world(g, {**PRIMITIVES, "InitCounter": src})
    assert node_state(world, "c") == {"count": 7, "tag": "x"}  # 增量合并,默认保留
    world.run()  # 源节点播种
    assert deliveries(world, "c", "out") == [7]  # 初始状态参与首轮执行


def test_init_sees_merged_config_and_assets():
    """init 可见合并后 config(config_defaults ∪ spec.config)与已解析 assets(§7)。"""

    def init(ctx):
        first = ctx.assets["db"].query("SELECT 1")[0]
        return {"label": f"{ctx.config['mode']}:{ctx.config['start']}:{first}"}

    def tick(ctx):
        return TickOutput(data_out={"out": ctx.state["label"]})

    node_type = NodeType(
        name="InitBoth",
        data_out=(DataOut("out"),),
        asset_in=(AssetIn("db", DatabaseCapability),),
        state_defaults={"label": None},
        config_defaults={"mode": "plain", "start": 0},
        tick=tick,
        init=init,
    )
    assets = FakeAssetSystem()
    ref = assets.create_db("sqlite://init")
    g = GraphDefinition("init_both")
    g.add_node("n", "InitBoth", start=3)  # spec.config 覆盖 config_defaults
    g.bind_asset("n", "db", ref.asset_id)
    result = GraphInstance.build(g, {**PRIMITIVES, "InitBoth": node_type}, asset_resolver=assets)
    assert result.ok, result.errors
    world = result.instance
    assert node_state(world, "n")["label"] == f"plain:3:{ref.asset_id}:SELECT 1"


def test_init_value_domain_rejected_at_build():
    """init 写入不可复制值 → BuildReport error,instance is None(§7)。"""

    def init(ctx):
        return {"obj": LockedDatabase()}  # 带锁对象:deepcopy 必然失败

    node_type = NodeType(name="BadInitValue", state_defaults={"obj": None}, init=init)
    g = GraphDefinition("bad_value")
    g.add_node("n", "BadInitValue")
    result = GraphInstance.build(g, {**PRIMITIVES, "BadInitValue": node_type})
    assert not result.ok
    assert result.instance is None
    assert any("non-copyable" in e for e in result.errors)


def test_init_unknown_key_rejected_at_build():
    """init 写入未声明状态字段 → BuildReport error(§7)。"""

    def init(ctx):
        return {"ghost": 1}  # 不在 state_defaults

    node_type = NodeType(name="BadInitKey", state_defaults={"real": 0}, init=init)
    g = GraphDefinition("bad_key")
    g.add_node("n", "BadInitKey")
    result = GraphInstance.build(g, {**PRIMITIVES, "BadInitKey": node_type})
    assert not result.ok
    assert result.instance is None
    assert any("undeclared state fields" in e for e in result.errors)


def test_init_raise_becomes_build_error():
    """init 抛异常 → BuildReport error(构建期分层,非 KIND_ERROR)(§7)。"""

    def init(ctx):
        raise RuntimeError("no start")

    node_type = NodeType(name="BadInitRaise", state_defaults={"v": 0}, init=init)
    g = GraphDefinition("bad_raise")
    g.add_node("n", "BadInitRaise")
    result = GraphInstance.build(g, {**PRIMITIVES, "BadInitRaise": node_type})
    assert not result.ok
    assert result.instance is None
    assert any("init raised" in e for e in result.errors)


def test_init_default_none_preserves_state_defaults():
    """init 默认 None = 既存行为:状态 = state_defaults 逐位不变(§7)。"""
    node_type = NodeType(name="NoInit", state_defaults={"v": [1, 2]})
    assert node_type.to_dict()["has_init"] is False
    g = GraphDefinition("no_init")
    g.add_node("n", "NoInit")
    world = make_world(g, {**PRIMITIVES, "NoInit": node_type})
    assert node_state(world, "n") == {"v": [1, 2]}


def test_source_node_init_runs_once_per_build():
    """源节点 init 每构建一次,非每 epoch(§2,7)。"""
    calls = {"n": 0}

    def init(ctx):
        calls["n"] += 1
        return {"count": 0}

    def tick(ctx):
        return TickOutput(state={"count": ctx.state["count"] + 1})

    src = NodeType(name="InitSource", state_defaults={"count": 0}, tick=tick, init=init)
    g = GraphDefinition("init_source")
    g.add_node("s", "InitSource")
    world = make_world(g, {**PRIMITIVES, "InitSource": src})
    world.run()
    world.run()
    world.run()
    assert calls["n"] == 1  # 构建期一次
    assert node_state(world, "s")["count"] == 3  # 每 epoch 播种执行


def test_initcontext_field_lockdown():
    """InitContext 字段白名单 {config, assets};TickContext 形状未变(§7)。"""
    assert set(InitContext.__dataclass_fields__) == {"config", "assets"}
    assert set(TickContext.__dataclass_fields__) == {"group", "data_in", "state", "config", "assets"}
    assert InitContext is not TickContext  # 构建期上下文独立于执行期上下文


# ==================================================================== Activation / Event 契约(§6)
def test_activation_event_boundary():
    """两 epoch 场景:激活不产事件 → 当轮静止;宿主注入"后来"的事件 →
    恢复传播驱动下游;内核两轮之间不存在节点等待状态(§6)。

    证明:现有事件注入机制已足以表达"节点未来产事件"——不是给 ref 增加
    异步执行能力,而是锁定该语义。
    """

    def tick(ctx):
        if ctx.group == "ask":
            # 一次 Activation:只记录"已请求",不产事件。节点执行域内可任意久;
            # 内核不等待——当轮传播照常静止。
            return TickOutput(state={"requested": ctx.data_in["q"]})
        # done 组:宿主注入的完成事件重新进入内核 → 产出答案
        return TickOutput(data_out={"out": ctx.data_in["done"]}, state={"requested": None})

    requestor = NodeType(
        name="AsyncRequestor",
        data_in=(DataIn("q"),),
        data_out=(DataOut("out"),),
        trigger_in=(TriggerIn("done"),),
        state_defaults={"requested": None},
        groups=(
            InputGroup("ask", inputs=("q",), policy=Policy.ON_ANY_DATA),
            InputGroup("done", triggers=("done",), policy=Policy.ON_TRIGGER),
        ),
        tick=tick,
    )
    g = GraphDefinition("activation_event")
    g.add_node("r", "AsyncRequestor")
    g.add_node("sink", "Sink")
    g.wire("r", "out", "sink", "in")
    world = make_world(g, {**PRIMITIVES, "AsyncRequestor": requestor})

    # epoch N:激活、不产事件;当轮传播照常静止,下游未被驱动
    world.run([Injection("r", "q", SLOT_DATA, Kind.DATA, "what is eidolon?")])
    assert deliveries(world, "r", "out") == []  # 尚未产事件
    assert node_state(world, "r")["requested"] == "what is eidolon?"  # 状态跨激活持久
    assert node_state(world, "sink")["last"] is None
    assert quiesces(world) == 1  # 当轮静止

    # epoch N+1:宿主注入节点"后来"产出的事件(新传播输入)→ 恢复传播
    world.run([Injection("r", "done", SLOT_TRIGGER, Kind.DATA, "a graph runtime")])
    assert node_state(world, "sink")["last"] == "a graph runtime"
    assert quiesces(world) == 2
    assert errors(world) == []  # 全程无错误:内核从未"等待"过节点
