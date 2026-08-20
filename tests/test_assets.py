"""资产层边界验证：graph-assets.md §9 七条已验证边界 + 编辑期结构检查。

依据：graph-assets.md §2(六条原则) / §6(错误分层) / §7(裁定：声明即必须) / §8(API 形状)
以假 AssetSystem + AssetResolver + Capability（tests/fake_assets.py）驱动：
节点只持有引用（AssetRef → Capability），创建与生命周期全部在资产系统；
声明的资产槽位构建期必须绑定并解析成功，降级经 Null 资产，内核永不出 None。

graph-asset-protocols.md §27 的协议边界用例（5-10），以及值域裁定与
调用顺序裁定用例（2026-08-20）。
"""

from eidolon_graph_ref.engine.instance import GraphInstance
from eidolon_graph_ref.engine.protocol import TickOutput
from eidolon_graph_ref.engine.timeline import KIND_DELIVER
from eidolon_graph_ref.model.assets import AssetIn
from eidolon_graph_ref.model.graph import GraphDefinition
from eidolon_graph_ref.model.node_type import NodeType
from eidolon_graph_ref.model.ports import DataOut
from eidolon_graph_ref.model.validate import validate
from eidolon_graph_ref.primitives import PRIMITIVES

from conftest import errors, fired, node_state, quiesces
from fake_assets import (
    CacheCapability,
    DatabaseCapability,
    FakeAssetSystem,
    FakeDatabase,
    FakeNullDatabase,
    LockedDatabase,
)


# --------------------------------------------------------------------- 测试节点


def define_db_probe(slot: str = "database", capability=DatabaseCapability) -> NodeType:
    """源节点探针：每 epoch 记录本节点看到的能力实例身份（asset_id）。

    声明即必须：tick 内无需 None 分支，ctx.assets[slot] 恒为真实 Capability。
    """

    def tick(ctx):
        cap = ctx.assets[slot]
        return TickOutput(state={"seen": ctx.state["seen"] + [cap.asset_id]})

    return NodeType(
        name="DbProbe",
        asset_in=(AssetIn(slot, capability),),
        state_defaults={"seen": []},
        tick=tick,
    )


def define_db_query() -> NodeType:
    """源节点：每 epoch 查询资产一次（运行期失效用例）。"""

    def tick(ctx):
        rows = ctx.assets["database"].query("SELECT 1")
        return TickOutput(data_out={"out": rows})

    return NodeType(
        name="DbQuery",
        data_out=(DataOut("out"),),
        asset_in=(AssetIn("database", DatabaseCapability),),
        tick=tick,
    )


def types_with(*node_types: NodeType) -> dict:
    return {**PRIMITIVES, **{t.name: t for t in node_types}}


# --------------------------------------------------------------------- §9 边界


def test_boundary_1_shared_asset_is_same_instance():
    """同一 asset_id → 同一底层实例（共享是自然引用关系，不是特殊模式）。"""
    assets = FakeAssetSystem()
    ref = assets.create_db("postgres://main")
    g = GraphDefinition("share")
    g.add_node("a", "DbProbe")
    g.add_node("b", "DbProbe")
    g.bind_asset("a", "database", ref.asset_id)
    g.bind_asset("b", "database", ref.asset_id)
    result = GraphInstance.build(g, types_with(define_db_probe()), asset_resolver=assets)
    assert result.ok
    world = result.instance

    world.run()
    assert node_state(world, "a")["seen"] == [ref.asset_id]
    assert node_state(world, "b")["seen"] == [ref.asset_id]
    # 同一底层实例：两个节点拿到的是同一个能力对象
    assert world.assets["a"]["database"] is world.assets["b"]["database"]
    assert world.assets["a"]["database"] is assets.instance(ref.asset_id)


def test_boundary_2_same_params_create_independent_assets():
    """相同参数创建两个独立资产：实例身份独立于创建参数（§2-3）。"""
    assets = FakeAssetSystem()
    r1 = assets.create_db("postgres://same")
    r2 = assets.create_db("postgres://same")
    assert r1.asset_id != r2.asset_id
    assert assets.resolve(r1) is not assets.resolve(r2)


def test_boundary_3_unbound_or_missing_asset_is_build_error():
    """声明即必须（§7 裁定）：资产未绑定/不存在 → BuildReport error，不存在实例（§6）。"""
    g = GraphDefinition("unbound")
    g.add_node("a", "DbProbe")  # 声明了资产槽但未绑定
    result = GraphInstance.build(g, types_with(define_db_probe()), asset_resolver=FakeAssetSystem())
    assert not result.ok
    assert result.instance is None
    assert any("asset slot 'database' is not bound" in e for e in result.errors)

    # 绑定到资产系统中不存在的 asset_id 同样是构建期错误
    g2 = GraphDefinition("missing")
    g2.add_node("a", "DbProbe")
    g2.bind_asset("a", "database", "db-404")
    result2 = GraphInstance.build(g2, types_with(define_db_probe()), asset_resolver=FakeAssetSystem())
    assert not result2.ok
    assert result2.instance is None


def test_boundary_3_graceful_degradation_uses_null_asset():
    """降级需求由资产系统提供 Null 资产（真实 Capability）：内核永不出 None。"""
    assets = FakeAssetSystem()
    ref = assets.create_null_db()  # 无真实后端环境下的降级资产
    g = GraphDefinition("degrade")
    g.add_node("a", "DbProbe")
    g.bind_asset("a", "database", ref.asset_id)
    result = GraphInstance.build(g, types_with(define_db_probe()), asset_resolver=assets)
    assert result.ok
    world = result.instance

    world.run()
    assert node_state(world, "a")["seen"] == [ref.asset_id]
    # 真实 Capability：节点代码无 None 分支，直接可用
    assert world.assets["a"]["database"] is assets.instance(ref.asset_id)
    assert world.assets["a"]["database"].query("SELECT 1") == []


def test_boundary_4_type_mismatch_collects_all_errors_at_once():
    """类型错误 → BuildReport error；多个错误一次收集（§7 裁定）。"""
    assets = FakeAssetSystem()
    cache_ref = assets.create_cache("redis://x")  # Cache 不是 Database
    g = GraphDefinition("multi")
    g.add_node("a", "DbProbe")
    g.add_node("b", "DbProbe")
    g.add_node("c", "DbProbe")
    g.bind_asset("a", "database", cache_ref.asset_id)  # 类型不符
    g.bind_asset("b", "database", "db-404")  # 解析失败
    # c：必需槽未绑定
    result = GraphInstance.build(g, types_with(define_db_probe()), asset_resolver=assets)
    assert not result.ok
    assert result.instance is None
    assert len(result.errors) == 3  # 一次收集全部错误
    assert any("type mismatch" in e for e in result.errors)


def test_boundary_5_instance_destruction_does_not_close_asset():
    """GraphInstance 销毁仅释放自身引用，不调用任何 close()（§6：所有权在资产系统）。"""
    assets = FakeAssetSystem()
    ref = assets.create_db("postgres://main")
    g = GraphDefinition("own")
    g.add_node("a", "DbProbe")
    g.bind_asset("a", "database", ref.asset_id)
    world = GraphInstance.build(g, types_with(define_db_probe()), asset_resolver=assets).instance
    world.run()

    del world  # 内核无 __del__ 关闭路径：销毁即丢引用
    assert not assets.instance(ref.asset_id).closed  # 资产仍开放
    assert assets.resolve(ref) is not None  # 资产系统仍可解析（未销毁）


def test_boundary_6_runtime_failure_is_kind_error_then_recovers():
    """运行期资产失效 → tick 异常 / KIND_ERROR，不产生任何传播事件；
    资产系统恢复后下一 epoch 正常执行（§6-7）。"""
    assets = FakeAssetSystem()
    ref = assets.create_db("postgres://main")
    g = GraphDefinition("fail")
    g.add_node("dbq", "DbQuery")
    g.add_node("sink", "Sink")
    g.wire("dbq", "out", "sink", "in")
    g.bind_asset("dbq", "database", ref.asset_id)
    world = GraphInstance.build(g, types_with(define_db_query()), asset_resolver=assets).instance

    assets.fail(ref.asset_id)  # 资产系统标记断线，Graph 不感知
    world.run()
    # 节点异常 → 既有 KIND_ERROR 语义；不产出任何输出事件
    assert len(errors(world)) == 1
    assert "ConnectionError" in errors(world)[0]
    assert node_state(world, "sink")["last"] is None  # 无数据到达
    assert [e for e in world.timeline.entries if e.kind == KIND_DELIVER and e.src_node == "dbq"] == []

    assets.recover(ref.asset_id)  # 资产系统后台恢复
    world.run()
    assert len(errors(world)) == 1  # 无新增错误
    assert node_state(world, "sink")["last"] == [f"{ref.asset_id}:SELECT 1"]


def test_boundary_7_observable_state_exposes_structural_facts_only():
    """observable_state 只暴露 ref/resolved 结构事实，绝不暴露对象（§8）。"""
    assets = FakeAssetSystem()
    ref = assets.create_db("postgres://main")
    g = GraphDefinition("view")
    g.add_node("a", "DbProbe")
    g.add_node("b", "DbProbe")
    g.bind_asset("a", "database", ref.asset_id)
    g.bind_asset("b", "database", ref.asset_id)  # 同资产共享
    world = GraphInstance.build(g, types_with(define_db_probe()), asset_resolver=assets).instance
    world.run()

    view = world.observable_state()
    assert view["a"]["assets"]["database"] == {"ref": ref.asset_id, "resolved": True}
    assert view["b"]["assets"]["database"] == {"ref": ref.asset_id, "resolved": True}

    # 可序列化形态：整个 observable_state 不含任何能力对象
    def all_plain(value) -> bool:
        if isinstance(value, dict):
            return all(all_plain(v) for v in value.values())
        if isinstance(value, (list, tuple)):
            return all(all_plain(v) for v in value)
        return value is None or isinstance(value, (str, int, float, bool))

    assert all_plain(view)


# --------------------------------------------------------------------- 编辑期结构检查


def test_validate_binding_must_reference_declared_slot():
    """绑定引用未声明的槽 → 编辑期结构错误（§8：资产是否存在是运行期问题，不检查）。"""
    g = GraphDefinition("slot")
    g.add_node("a", "Sink")  # Sink 无 asset_in 声明
    g.bind_asset("a", "database", "db-1")
    result = validate(g, PRIMITIVES)
    assert any("declares no asset slot 'database'" in e for e in result.errors)


def test_validate_binding_must_reference_existing_node():
    g = GraphDefinition("node")
    g.bind_asset("ghost", "database", "db-1")
    result = validate(g, PRIMITIVES)
    assert any("unknown node 'ghost'" in e for e in result.errors)


def test_duplicate_binding_raises():
    """绑定唯一：(node, slot) 重复绑定即报错（§8）。"""
    g = GraphDefinition("dup")
    g.add_node("a", "DbProbe")
    g.bind_asset("a", "database", "db-1")
    try:
        g.bind_asset("a", "database", "db-2")
        raise AssertionError("duplicate binding must raise")
    except ValueError:
        pass


def test_build_without_assets_and_resolver_still_works():
    """无资产声明、无 resolver 的图照常构建（构建 API 对既有用法向后兼容）。"""
    g = GraphDefinition("plain")
    g.add_node("src", "Source", step=0)
    g.add_node("sink", "Sink")
    g.wire("src", "out", "sink", "in")
    result = GraphInstance.build(g, PRIMITIVES)
    assert result.ok
    result.instance.run()
    assert node_state(result.instance, "sink")["last"] == 0
    assert result.instance.observable_state()["sink"]["assets"] == {}


def test_direct_construction_rejected():
    """裸构造被拒绝：GraphInstance.build 是唯一正式入口（完成条件④）。

    直接构造绕过结构校验与资产解析，产生"类型合法但语义非法"的实例——
    与"声明即必须"矛盾（graph-assets.md §6-7）。
    """
    g = GraphDefinition("bare")
    g.add_node("a", "DbProbe")
    try:
        GraphInstance(g, types_with(define_db_probe()))
        raise AssertionError("direct construction must raise")
    except TypeError as exc:
        assert "build()" in str(exc)


# --------------------------------------------------------------------- tick 契约


def test_tick_cannot_mutate_asset_store():
    """fire 传入浅拷贝：tick 插入/改写 ctx.assets 不影响节点 store（§8 实现要点）。"""
    assets = FakeAssetSystem()
    ref = assets.create_db("postgres://main")

    def tick(ctx):
        ctx.assets["hacked"] = "x"
        ctx.assets["database"] = None
        return TickOutput()

    probe = NodeType(
        name="HackyProbe",
        asset_in=(AssetIn("database", DatabaseCapability),),
        tick=tick,
    )
    g = GraphDefinition("hack")
    g.add_node("a", "HackyProbe")
    g.bind_asset("a", "database", ref.asset_id)
    world = GraphInstance.build(g, types_with(probe), asset_resolver=assets).instance
    world.run()

    assert world.assets["a"]["database"] is assets.instance(ref.asset_id)  # 仍是原能力对象
    assert "hacked" not in world.assets["a"]


# --------------------------------------------------------------------- 协议边界(graph-asset-protocols.md §27-5~8)


def test_protocol_boundary_asset_debugged_without_graph():
    """资产独立调试：create → 直接调用使用面 → 断言，不经任何节点与图（§14）。"""
    assets = FakeAssetSystem()
    ref = assets.create_db("postgres://standalone")

    # 能力测试：直接调用、断言——无需 Graph / Node / 事件传播链 / Runtime
    cap = assets.resolve(ref)
    assert cap.query("SELECT 1") == [f"{ref.asset_id}:SELECT 1"]
    assert cap.query("SELECT 2") == [f"{ref.asset_id}:SELECT 2"]
    assert cap.calls == ["SELECT 1", "SELECT 2"]
    # 使用面独立于构建期也可断言协议一致
    assert isinstance(cap, DatabaseCapability)

    # 失效/恢复也是资产系统级的独立行为（不经图）
    assets.fail(ref.asset_id)
    try:
        cap.query("SELECT 3")
        raise AssertionError("failed asset must raise")
    except ConnectionError:
        pass
    assets.recover(ref.asset_id)
    assert cap.query("SELECT 3") == [f"{ref.asset_id}:SELECT 3"]


def test_protocol_boundary_node_debugged_with_fake_asset_and_swappable():
    """节点独立调试：注入假资产验证编排；换绑定实现零节点改动（§14/§17）。"""

    def define_orchestrator() -> NodeType:
        """编排节点：决定调用顺序与结果传播；能力实现完全不在此。"""

        def tick(ctx):
            db = ctx.assets["database"]
            return TickOutput(data_out={"out": db.query("first") + db.query("second")})

        return NodeType(
            name="Orchestrator",
            data_out=(DataOut("out"),),
            asset_in=(AssetIn("database", DatabaseCapability),),
            tick=tick,
        )

    assets = FakeAssetSystem()
    orchestrator = define_orchestrator()  # 同一个节点类型，两种资产实现

    # 编排测试：注入记录型假资产，验证调用顺序与结果传播
    ref = assets.create_db("postgres://main")
    g = GraphDefinition("orch")
    g.add_node("orch", "Orchestrator")
    g.add_node("sink", "Sink")
    g.wire("orch", "out", "sink", "in")
    g.bind_asset("orch", "database", ref.asset_id)
    world = GraphInstance.build(g, types_with(orchestrator), asset_resolver=assets).instance
    world.run()
    assert assets.instance(ref.asset_id).calls == ["first", "second"]  # 编排行为：顺序两次调用
    assert node_state(world, "sink")["last"] == [f"{ref.asset_id}:first", f"{ref.asset_id}:second"]

    # 替换实现：同一节点类型，换绑 Null 资产——节点零改动（§17）
    null_ref = assets.create_null_db()
    g2 = GraphDefinition("orch2")
    g2.add_node("orch", "Orchestrator")
    g2.add_node("sink", "Sink")
    g2.wire("orch", "out", "sink", "in")
    g2.bind_asset("orch", "database", null_ref.asset_id)
    world2 = GraphInstance.build(g2, types_with(orchestrator), asset_resolver=assets).instance
    world2.run()
    assert node_state(world2, "sink")["last"] == []  # 降级实现同样满足编排


def test_protocol_boundary_management_surface_not_in_node_contract():
    """协议表面分离：节点契约（Capability Protocol）不含管理方法（§1.1/§4）。

    反射绕过属契约外行为，不防御：注入对象底层可能带有管理属性，协议未
    声明，内核不做任何隐藏/防御（graph-assets.md §2-5 信任模型）。
    """
    assets = FakeAssetSystem()
    ref = assets.create_db("postgres://main")
    cap = assets.resolve(ref)

    # 契约层：使用面 Protocol 只声明业务方法
    assert not hasattr(DatabaseCapability, "close")
    assert not hasattr(DatabaseCapability, "health")
    assert not hasattr(DatabaseCapability, "reconnect")
    assert not hasattr(DatabaseCapability, "destroy")

    # 信任模型：底层实例的管理属性经反射可达，内核不防御（契约外行为）
    assert hasattr(cap, "closed")
    assert hasattr(cap, "failed")
    # 但构建期类型检查只按契约：满足使用面即通过，管理面不参与
    g = GraphDefinition("surfaces")
    g.add_node("a", "DbProbe")
    g.bind_asset("a", "database", ref.asset_id)
    result = GraphInstance.build(g, types_with(define_db_probe()), asset_resolver=assets)
    assert result.ok


def test_protocol_boundary_asset_never_drives_runtime():
    """单向控制：Runtime → Node → Asset（§15）。

    资产在 tick 内做复杂工作，但它自身不产生任何时间线条目——只有节点
    产出的事件才进入图运行时；资产调用结果必须经节点转化为传播事实。
    """
    assets = FakeAssetSystem()
    ref = assets.create_db("postgres://main")
    g = GraphDefinition("oneway")
    g.add_node("dbq", "DbQuery")
    g.add_node("sink", "Sink")
    g.wire("dbq", "out", "sink", "in")
    g.bind_asset("dbq", "database", ref.asset_id)
    world = GraphInstance.build(g, types_with(define_db_query()), asset_resolver=assets).instance
    world.run()

    # 时间线中的事件生产者只能是节点或宿主（None）：资产从不出现
    producers = {e.producer for e in world.timeline.events.values()}
    assert "dbq" in producers
    assert producers <= {None, "dbq"}
    assert ref.asset_id not in producers
    # 资产做了工作（能力内部有痕迹），但工作痕迹只存在于资产自身，不进图运行时
    assert assets.instance(ref.asset_id).calls == ["SELECT 1"]
    assert quiesces(world) == 1


# --------------------------------------------------------------------- 值域裁定与调用顺序裁定(2026-08-20)


def test_state_rejects_non_copyable_capability():
    """值域裁定:State 值域 = Value。不可复制的能力对象写入 state →
    KIND_ERROR + 拒绝提交(与"未声明字段"同层);后续 epoch 照常执行,
    不再炸出 run()。"""
    cap = LockedDatabase()

    class Resolver:
        def resolve(self, ref):
            return cap

    def tick(ctx):
        return TickOutput(state={"held": ctx.assets["database"]})  # 契约外:能力对象混入状态平面

    probe = NodeType(
        name="StateHolder",
        asset_in=(AssetIn("database", DatabaseCapability),),
        state_defaults={"held": None},
        tick=tick,
    )
    g = GraphDefinition("leak")
    g.add_node("a", "StateHolder")
    g.bind_asset("a", "database", "db-1")
    world = GraphInstance.build(g, types_with(probe), asset_resolver=Resolver()).instance

    world.run()
    assert world.node_states["a"]["held"] is None  # 提交被拒,状态未变
    assert any("non-copyable" in e for e in errors(world))
    world.run()  # 第二轮照常执行(此前会因 fire 的 deepcopy 炸出 run())
    assert any("non-copyable" in e for e in errors(world))


def test_state_allows_copyable_objects_as_committed_original():
    """值域裁定边界:可复制的对象(FakeDatabase 恰好可复制)原样提交——判据
    是可复制性,内核无法识别"伪能力"——契约外,信任模型不防御(零拷贝)。"""
    assets = FakeAssetSystem()
    ref = assets.create_db("postgres://main")

    def tick(ctx):
        return TickOutput(state={"held": ctx.assets["database"]})

    probe = NodeType(
        name="StateHolder2",
        asset_in=(AssetIn("database", DatabaseCapability),),
        state_defaults={"held": None},
        tick=tick,
    )
    g = GraphDefinition("leak2")
    g.add_node("a", "StateHolder2")
    g.bind_asset("a", "database", ref.asset_id)
    world = GraphInstance.build(g, types_with(probe), asset_resolver=assets).instance
    world.run()
    # 提交成功,原对象进入状态(零拷贝;信任模型不防御"伪能力")
    assert world.node_states["a"]["held"] is assets.instance(ref.asset_id)
    assert world.node_states["a"]["held"].query("x") == [f"{ref.asset_id}:x"]


def test_data_output_rejects_non_copyable_payload():
    """值域裁定:data_out 产出不可复制载荷 → KIND_ERROR + 不产出事件。"""
    cap = LockedDatabase()

    class Resolver:
        def resolve(self, ref):
            return cap

    def tick(ctx):
        return TickOutput(data_out={"out": ctx.assets["database"]})

    shipper = NodeType(
        name="CapShipper",
        data_out=(DataOut("out"),),
        asset_in=(AssetIn("database", DatabaseCapability),),
        tick=tick,
    )
    g = GraphDefinition("ship")
    g.add_node("ship", "CapShipper")
    g.add_node("sink", "Sink")
    g.wire("ship", "out", "sink", "in")
    g.bind_asset("ship", "database", "db-1")
    world = GraphInstance.build(g, types_with(shipper), asset_resolver=Resolver()).instance
    world.run()
    assert any("non-copyable" in e for e in errors(world))
    assert node_state(world, "sink")["last"] is None  # 无事件到达


def test_data_output_copyable_objects_pass_as_original():
    """值域裁定边界:可复制的假能力原样作为 data 载荷传播(探针只校验不
    复制,数据平面零拷贝)——下游拿到原对象(判据 = 可复制性,信任模型)。"""
    assets = FakeAssetSystem()
    ref = assets.create_db("postgres://main")

    def tick(ctx):
        return TickOutput(data_out={"out": ctx.assets["database"]})

    shipper = NodeType(
        name="CapShipper2",
        data_out=(DataOut("out"),),
        asset_in=(AssetIn("database", DatabaseCapability),),
        tick=tick,
    )
    g = GraphDefinition("ship2")
    g.add_node("ship", "CapShipper2")
    g.add_node("sink", "Sink")
    g.wire("ship", "out", "sink", "in")
    g.bind_asset("ship", "database", ref.asset_id)
    world = GraphInstance.build(g, types_with(shipper), asset_resolver=assets).instance
    world.run()
    assert world.node_states["sink"]["last"] is assets.instance(ref.asset_id)
    assert world.node_states["sink"]["last"].query("x") == [f"{ref.asset_id}:x"]


def test_shared_asset_call_order_is_not_kernel_semantics():
    """共享资产调用顺序不构成 Runtime 语义(2026-08-20 裁定):内核不承诺、
    不检查声明序;若共享可变资产因顺序不同产生不同结果,属节点编排与资产
    可共享性声明问题(资产系统/编排负责)。本测试只锁编排事实:两节点都
    调用了能力。"""
    assets = FakeAssetSystem()
    ref = assets.create_db("postgres://main")
    g = GraphDefinition("order")
    g.add_node("a", "DbQuery")
    g.add_node("b", "DbQuery")
    g.bind_asset("a", "database", ref.asset_id)
    g.bind_asset("b", "database", ref.asset_id)
    world = GraphInstance.build(g, types_with(define_db_query()), asset_resolver=assets).instance
    world.run()
    # 只锁"两个节点都完成调用"的编排事实,不锁先后序
    assert sorted(assets.instance(ref.asset_id).calls) == ["SELECT 1", "SELECT 1"]
    assert len([f for f in fired(world, 1) if f[0] in ("a", "b")]) == 2


def test_same_definition_builds_for_multiple_environments():
    """同一图定义 × 多个资产环境:图是纯描述,可反复构建,环境差异集中在
    资产系统,实例互不污染(§18/§22)。"""
    g = GraphDefinition("env")
    g.add_node("a", "DbProbe")
    g.bind_asset("a", "database", "main_db")

    prod = FakeAssetSystem()
    prod._assets["main_db"] = FakeDatabase(asset_id="main_db", uri="postgres://prod")
    prod_world = GraphInstance.build(g, types_with(define_db_probe()), asset_resolver=prod).instance

    local = FakeAssetSystem()
    local._assets["main_db"] = FakeNullDatabase(asset_id="main_db")
    local_world = GraphInstance.build(g, types_with(define_db_probe()), asset_resolver=local).instance

    prod_world.run()
    local_world.run()
    assert node_state(prod_world, "a")["seen"] == ["main_db"]
    assert node_state(local_world, "a")["seen"] == ["main_db"]
    # 实例互不污染:同一 asset_id 在两个环境是两个实例
    assert prod_world.assets["a"]["database"] is not local_world.assets["a"]["database"]
    # 图定义本身未被构建污染(纯描述,可反复构建)
    assert g.asset_bindings[("a", "database")].asset_id == "main_db"


def test_asset_identity_stable_across_epochs():
    """跨 epoch 资产身份稳定:重连语义的前提(能力对象身份不变,§21)。"""
    assets = FakeAssetSystem()
    ref = assets.create_db("postgres://main")
    g = GraphDefinition("stable")
    g.add_node("a", "DbProbe")
    g.bind_asset("a", "database", ref.asset_id)
    world = GraphInstance.build(g, types_with(define_db_probe()), asset_resolver=assets).instance
    cap = world.assets["a"]["database"]
    for _ in range(3):
        world.run()
        assert world.assets["a"]["database"] is cap
        assert world.assets["a"]["database"] is assets.instance(ref.asset_id)
