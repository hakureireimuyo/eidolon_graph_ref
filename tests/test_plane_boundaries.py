"""平面边界攻击测试：故意把每一种东西塞进"不属于它的平面"。

依据：graph-assets.md §2-4（三平面正交）+ graph-asset-protocols.md（单向控制、
协议表面分离、Graph/Asset 解耦）。目标不是证明代码能运行，而是寻找
"职责边界穿透"——每行矩阵对应一个裁定问题，测试锁定**当前现状**，
未决部分供值域裁定（State/Data 值域、共享调用顺序）。

矩阵总览（来源 → 目标 = 是否允许越权）：

| 来源 | 目标 | 裁定问题 | 测试 |
|---|---|---|---|
| Asset | State | Capability 进入 State | `test_assets.py::test_state_rejects_non_copyable_capability`（✓ 值域裁定：不可复制 → KIND_ERROR 拒提交；可复制 → 原对象通过） |
| Asset | Data | Capability 作为 Data 传播 | `test_assets.py::test_data_output_rejects_non_copyable_payload`（✓ 值域裁定：不可复制 → 拒绝产出；可复制 → 原样传播） |
| Asset | Event | Capability 成为 Event 载荷 | 本文件 `test_injection_enforces_value_domain`（✓ 值域裁定：不可复制注入 → ValueError；可复制 → 原样通过） |
| Asset | Runtime | Asset 主动产生 Runtime Event | `test_assets.py::test_protocol_boundary_asset_never_drives_runtime`（✓ 已裁定禁止：单向控制） |
| State | Asset | State 反向提供 Capability | 本文件 `test_state_can_supply_capability_back_to_tick_original`（值域裁定下弱化：仅可复制对象自强化） |
| Data | Asset | Data 改变 Asset 身份 | 本文件 `test_data_cannot_rebind_asset_identity`（✓ 已裁定禁止：身份只在构建期决定） |
| Event | Asset | Event 直接驱动 Asset 生命周期 | 本文件 `test_event_cannot_drive_asset_lifecycle`（✓ 已裁定禁止：只有资产系统能触碰） |
| Runtime | Asset | Runtime 管理 Asset 生命周期 | `test_assets.py::test_boundary_5_instance_destruction_does_not_close_asset`（✓ 已裁定禁止：所有权在资产系统） |
| Node | Asset | Node 创建/销毁 Asset | 本文件 `test_node_has_no_asset_system_handle`（✓ 已裁定禁止：ctx 无资产系统入口，协议无生命周期方法） |
| Asset | Graph | Asset 反向修改 Graph | 本文件 `test_asset_cannot_modify_graph_definition`（✓ 已裁定禁止） |
| Graph | Asset System | Graph 携带初始化参数 | 本文件 `test_graph_cannot_carry_init_params`（✓ 已裁定禁止：AssetRef 纯身份） |
| Asset System | Graph | Asset System 修改 Graph Definition | 本文件 `test_asset_cannot_modify_graph_definition`（✓ 已裁定禁止：资产系统拿不到图） |

值域裁定（2026-08-20）：State/Data/Event 载荷的值域 = Value，Capability
不得入内；内核以可复制性（deepcopy 探针）为执行判据。探针只校验不复制
——数据平面保持零拷贝（扇出共享载荷引用是锁定内核事实）。恰好可复制的
"伪能力"对象属契约外（与反射绕过同一信任模型），原样通过。

值域入口矩阵（破坏式验证：每个进入 Value 平面的入口都被攻击过）：

```text
入口                    Capability           Value
Node tick → state        ×(KIND_ERROR 拒提交)  ✓
Node tick → data_out     ×(KIND_ERROR 拒产出)  ✓
Host Injection           ×(ValueError)         ✓
Graph build → config     ×(BuildReport)        ✓
Snapshot restore         N/A（机制未实现，实现缺口⑤，非语义缺口）
```

另含两个架构级不变量：GraphDefinition 纯度（构建不写回图）与多实例
隔离（共享/独立由 AssetSystem 决定，Runtime 不参与）。
"""

from eidolon_graph_ref.engine.event import Injection, Kind
from eidolon_graph_ref.engine.instance import GraphInstance
from eidolon_graph_ref.engine.protocol import TickContext, TickOutput
from eidolon_graph_ref.model.assets import AssetIn, AssetRef
from eidolon_graph_ref.model.graph import GraphDefinition, SLOT_DATA
from eidolon_graph_ref.model.node_type import NodeType
from eidolon_graph_ref.model.ports import DataOut
from eidolon_primitives import PRIMITIVES

from fake_assets import (
    DatabaseCapability,
    FakeAssetSystem,
    FakeDatabase,
    FakeNullDatabase,
    LockedDatabase,
)


# --------------------------------------------------------------------- 测试节点


def probe_type() -> NodeType:
    """源节点探针：记录本节点看到的能力实例身份。"""

    def tick(ctx):
        cap = ctx.assets["database"]
        return TickOutput(state={"seen": ctx.state["seen"] + [cap.asset_id]})

    return NodeType(
        name="DbProbe",
        asset_in=(AssetIn("database", DatabaseCapability),),
        state_defaults={"seen": []},
        tick=tick,
    )


def query_type() -> NodeType:
    """源节点：每 epoch 查询资产一次。"""

    def tick(ctx):
        return TickOutput(data_out={"out": ctx.assets["database"].query("SELECT 1")})

    return NodeType(
        name="DbQuery",
        data_out=(DataOut("out"),),
        asset_in=(AssetIn("database", DatabaseCapability),),
        tick=tick,
    )


def types_with(*node_types: NodeType) -> dict:
    return {**PRIMITIVES, **{t.name: t for t in node_types}}


# --------------------------------------------------------------------- 矩阵用例


def test_injection_enforces_value_domain():
    """Asset → Event（载荷方向）：值域裁定——宿主注入不可复制载荷 →
    ValueError（宿主编程错误，fail fast）；可复制的假能力以副本形态通过
    （判据 = 可复制性，信任模型）。"""
    assets = FakeAssetSystem()
    ref = assets.create_db("postgres://main")
    g = GraphDefinition("injp")
    g.add_node("sink", "Sink")
    world = GraphInstance.build(g, PRIMITIVES, asset_resolver=assets).instance

    # 可复制的假能力：原样作为普通载荷通过（探针只校验不复制，零拷贝）
    world.run([Injection("sink", "in", SLOT_DATA, Kind.DATA, assets.instance(ref.asset_id))])
    assert world.node_states["sink"]["last"] is assets.instance(ref.asset_id)

    # 不可复制载荷：注入即拒绝（宿主可见）
    try:
        world.run([Injection("sink", "in", SLOT_DATA, Kind.DATA, LockedDatabase())])
        raise AssertionError("non-copyable injection must raise")
    except ValueError:
        pass


def test_state_can_supply_capability_back_to_tick_original():
    """State → Asset：可复制的假能力原样进入状态后，后续 tick 可从 state
    反向取得并使用——泄漏自强化仍存在（零拷贝，原对象）。值域判据是
    可复制性，可复制的"伪能力"属契约外（信任模型）。"""
    assets = FakeAssetSystem()
    ref = assets.create_db("postgres://main")

    def tick(ctx):
        held = ctx.state.get("held")
        if held is None:  # 第一轮：把能力对象写进 state（零拷贝提交）
            return TickOutput(state={"held": ctx.assets["database"]})
        # 第二轮起：从 state 反向取得能力并调用（不经 ctx.assets）
        return TickOutput(state={"result": held.query("from-state")})

    probe = NodeType(
        name="StateSource",
        asset_in=(AssetIn("database", DatabaseCapability),),
        state_defaults={"held": None, "result": None},
        tick=tick,
    )
    g = GraphDefinition("statecap")
    g.add_node("a", "StateSource")
    g.bind_asset("a", "database", ref.asset_id)
    world = GraphInstance.build(g, types_with(probe), asset_resolver=assets).instance

    world.run()
    assert world.node_states["a"]["held"] is assets.instance(ref.asset_id)  # 原对象(零拷贝)
    assert world.node_states["a"]["held"].query("probe") == [f"{ref.asset_id}:probe"]
    world.run()
    assert world.node_states["a"]["result"] == [f"{ref.asset_id}:from-state"]


def test_data_cannot_rebind_asset_identity():
    """Data → Asset：数据载荷（即使携带 asset_id 形状的字符串）不能改变
    绑定与注入身份——资产身份只在构建期由 AssetRef 决定（§8）。"""
    assets = FakeAssetSystem()
    ref_a = assets.create_db("postgres://a")
    ref_b = assets.create_db("postgres://b")
    g = GraphDefinition("rebind")
    g.add_node("probe", "DbProbe")
    g.add_node("sink", "Sink")
    g.bind_asset("probe", "database", ref_a.asset_id)
    world = GraphInstance.build(g, types_with(probe_type()), asset_resolver=assets).instance
    cap = world.assets["probe"]["database"]

    # 携带另一个 asset_id 的数据流过图
    for _ in range(2):
        world.run([Injection("sink", "in", SLOT_DATA, Kind.DATA, ref_b.asset_id)])
    # 绑定与注入身份纹丝不动
    assert world.assets["probe"]["database"] is cap
    assert world.assets["probe"]["database"] is assets.instance(ref_a.asset_id)


def test_event_cannot_drive_asset_lifecycle():
    """Event → Asset：事件只能驱动节点执行；资产生命周期位（failed/closed）
    不可能被任何事件直接改变——只有资产系统能触碰（协议 §22）。"""
    assets = FakeAssetSystem()
    ref = assets.create_db("postgres://main")
    g = GraphDefinition("evcap")
    g.add_node("dbq", "DbQuery")
    g.add_node("sink", "Sink")
    g.wire("dbq", "out", "sink", "in")
    g.bind_asset("dbq", "database", ref.asset_id)
    world = GraphInstance.build(g, types_with(query_type()), asset_resolver=assets).instance

    world.run([Injection("sink", "in", SLOT_DATA, Kind.DATA, "noise")])
    world.run([Injection("sink", "in", SLOT_DATA, Kind.DATA, "noise")])
    # 资产调用痕迹只来自节点 fire（dbq 每轮播种执行一次）；生命周期位从未被事件触碰
    assert assets.instance(ref.asset_id).calls == ["SELECT 1", "SELECT 1"]
    assert not assets.instance(ref.asset_id).failed
    assert not assets.instance(ref.asset_id).closed


def test_asset_cannot_modify_graph_definition():
    """Asset → Graph / Asset System → Graph：能力对象与资产系统都拿不到
    图；构建与运行全程，图定义（nodes/wires/asset_bindings）不发生任何
    变化——图是纯描述（协议 §18）。"""
    assets = FakeAssetSystem()
    ref = assets.create_db("postgres://main")
    g = GraphDefinition("graphro")
    g.add_node("a", "DbProbe")
    g.bind_asset("a", "database", ref.asset_id)
    before_nodes = dict(g.nodes)
    before_wires = tuple(g.wires)
    before_bindings = g.asset_bindings

    world = GraphInstance.build(g, types_with(probe_type()), asset_resolver=assets).instance
    world.run()
    world.run()

    assert g.nodes == before_nodes
    assert g.wires == before_wires
    assert g.asset_bindings == before_bindings


def test_node_has_no_asset_system_handle():
    """Node → Asset：节点契约里不存在创建/销毁资产的入口——TickContext 只
    暴露使用面，拿不到资产系统；能力协议与实例使用面均无生命周期方法
    （协议 §4/§13）。"""
    # TickContext 字段白名单：无资产系统句柄
    assert set(TickContext.__dataclass_fields__) == {"group", "data_in", "state", "config", "assets"}
    # 使用面协议无生命周期方法
    assert not hasattr(DatabaseCapability, "close")
    assert not hasattr(DatabaseCapability, "destroy")
    # 假资产实例的使用面上也无可调用的销毁方法（只有资产系统能 destroy）
    assets = FakeAssetSystem()
    ref = assets.create_db("postgres://main")
    cap = assets.resolve(ref)
    assert not hasattr(cap, "close")
    assert not hasattr(cap, "destroy")


def test_graph_cannot_carry_init_params():
    """Graph → Asset System：图只能携带资产身份；初始化参数被资产系统独占
    （§12），AssetRef 与 bind_asset 在结构上拒绝参数。"""
    try:
        AssetRef(asset_id="db-1", params={"dsn": "..."})
        raise AssertionError("AssetRef must reject creation params")
    except TypeError:
        pass

    g = GraphDefinition("params")
    g.add_node("a", "DbProbe")
    try:
        g.bind_asset("a", "database", "db-1", params={"dsn": "..."})
        raise AssertionError("bind_asset must reject creation params")
    except TypeError:
        pass


# --------------------------------------------------------------------- 值域入口补缺口与架构不变量


def test_config_rejects_non_copyable_value():
    """Graph build → config 入口：配置值域也是 Value——经 add_node 配置携带
    不可复制对象（Capability 走私）在构建期被 BuildReport 拦下（编辑与运行
    分离：图定义是纯描述，不持有活对象）。"""
    cap = LockedDatabase()
    g = GraphDefinition("cfgcap")
    g.add_node("a", "ConfigHolder", secret=cap)
    types = {
        **PRIMITIVES,
        "ConfigHolder": NodeType(
            name="ConfigHolder",
            config_defaults={"secret": None},
            tick=lambda ctx: TickOutput(),
        ),
    }
    result = GraphInstance.build(g, types)
    assert not result.ok
    assert result.instance is None
    assert any("config field 'secret' carries non-copyable" in e for e in result.errors)


def test_graph_definition_purity_across_builds():
    """GraphDefinition 纯度不变量：构建永远不能把 Capability / resolved
    Asset / 运行状态 / 环境写回图定义。serialize(before) == serialize(after)，
    即便在多个不同环境各构建运行一次。

    Graph 是描述；GraphInstance 是绑定了运行环境后的执行实体——这是
    Snapshot/Replay 能否干净工作的基础（协议 §18）。"""

    def serialize(g: GraphDefinition) -> str:
        return repr(
            {
                "name": g.name,
                "nodes": {nid: {"type": s.type, "config": s.config} for nid, s in g.nodes.items()},
                "wires": [(w.src_node, w.src_port, w.dst_node, w.dst_port, w.dst_slot) for w in g.wires],
                "asset_bindings": {(n, s): r.asset_id for (n, s), r in g.asset_bindings.items()},
            }
        )

    g = GraphDefinition("pure")
    g.add_node("dbq", "DbQuery")
    g.add_node("sink", "Sink")
    g.wire("dbq", "out", "sink", "in", slot=SLOT_DATA)  # 显式槽位：构建不触发推断副作用
    g.bind_asset("dbq", "database", "main_db")
    before = serialize(g)

    env_a = FakeAssetSystem()
    env_a._assets["main_db"] = FakeDatabase(asset_id="main_db", uri="postgres://a")
    env_b = FakeAssetSystem()
    env_b._assets["main_db"] = FakeDatabase(asset_id="main_db", uri="postgres://b")
    env_c = FakeAssetSystem()
    env_c._assets["main_db"] = FakeNullDatabase(asset_id="main_db")

    for env in (env_a, env_b, env_c):
        world = GraphInstance.build(g, types_with(query_type()), asset_resolver=env).instance
        world.run()
        world.run()

    assert serialize(g) == before


def test_multi_instance_isolation_and_sharing_by_asset_system():
    """同一图 × 多实例：节点状态与时间线互不污染；共享/独立由 AssetSystem
    决定（同一 ref → 同一能力对象；不同系统 → 不同对象），Runtime 不参与
    判定（协议 §11 与调用顺序裁定）。"""
    g = GraphDefinition("multi")
    g.add_node("probe", "DbProbe")
    g.bind_asset("probe", "database", "main_db")

    # 环境一：两个实例共享同一资产系统 → 同一能力对象（资产系统的共享裁定）
    shared = FakeAssetSystem()
    shared._assets["main_db"] = FakeDatabase(asset_id="main_db", uri="postgres://shared")
    world_a = GraphInstance.build(g, types_with(probe_type()), asset_resolver=shared).instance
    world_b = GraphInstance.build(g, types_with(probe_type()), asset_resolver=shared).instance
    assert world_a.assets["probe"]["database"] is world_b.assets["probe"]["database"]

    # 环境二：另一个资产系统 → 不同能力对象（独立实例是资产系统的裁定）
    other = FakeAssetSystem()
    other._assets["main_db"] = FakeDatabase(asset_id="main_db", uri="postgres://other")
    world_c = GraphInstance.build(g, types_with(probe_type()), asset_resolver=other).instance
    assert world_c.assets["probe"]["database"] is not world_a.assets["probe"]["database"]

    # A 运行两轮：只弄脏自己的状态与时间线
    world_a.run()
    world_a.run()
    assert world_a.node_states["probe"]["seen"] == ["main_db", "main_db"]
    assert world_b.node_states["probe"]["seen"] == []  # B 状态隔离
    assert world_b.timeline.entries == []  # B 时间线隔离
    assert world_b.run_no == 0

    # C 的运行同样不影响 B；探针节点不调用能力，资产痕迹各自独立
    world_c.run()
    assert world_c.node_states["probe"]["seen"] == ["main_db"]
    assert world_b.node_states["probe"]["seen"] == []
    assert other.instance("main_db").calls == []
    assert shared.instance("main_db").calls == []
