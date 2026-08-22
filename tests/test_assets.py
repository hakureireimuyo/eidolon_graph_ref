"""资产层边界锁定(压缩版,DSL v2 迁移后)。

依据:graph-assets.md §2-3, §7-8(编辑运行分离 / 声明即必须 / 身份独立于参数)。
此处保留核心不变量:

- 声明即必须:未绑定 / 解析失败 → BuildReport error(资产缺席是结构缺陷)
- 共享/独立完全由 asset_id 决定
- 降级由资产系统提供 Null 资产(真实 Capability)
- 运行期断线 → KIND_ERROR,恢复后继续
- 非拷贝能力进不了 state(deepcopy 判据)
- 绑定唯一:(node, slot) 重复绑定报错
"""

import pytest

from eidolon_dsl import Asset, NodeDefinition, State, group
from eidolon_graph_ref.engine import GraphInstance, Injection, Kind
from eidolon_graph_ref.engine.timeline import KIND_ERROR
from eidolon_graph_ref.model import GraphDefinition, SLOT_DATA
from eidolon_graph_ref.model.assets import AssetRef
from fake_assets import DatabaseCapability, FakeAssetSystem, LockedDatabase


class DbQuery(NodeDefinition):
    @group
    def query(this, db: Asset[DatabaseCapability], sql: str) -> list:
        return db.query(sql)


class DbRemember(NodeDefinition):
    """把能力对象写进 state——非拷贝能力必须被内核拒绝。"""

    conn: State = None

    @group
    def grab(this, db: Asset[DatabaseCapability], x: int) -> None:
        this.conn = db


def _build(type_, ref=None, resolver=None):
    g = GraphDefinition()
    g.add_node("n", type_.name)
    if ref is not None:
        g.bind_asset(node_id="n", slot="db", asset_id=ref.asset_id)
    return GraphInstance.build(g, {type_.name: type_}, asset_resolver=resolver)


# ==================================================================== 声明即必须
def test_unbound_slot_is_build_error():
    report = _build(DbQuery.TYPE, ref=None, resolver=FakeAssetSystem())
    assert not report.ok
    assert "asset slot 'db' is not bound" in report.errors[0]


def test_resolve_failure_is_build_error():
    class Broken:
        def resolve(self, ref):
            raise KeyError("unknown asset")

    report = _build(DbQuery.TYPE, ref=AssetRef("ghost"), resolver=Broken())
    assert not report.ok
    assert "asset resolve failed" in report.errors[0]


# ==================================================================== 身份语义
def test_shared_and_independent_by_asset_id():
    system = FakeAssetSystem()
    shared = system.create_db("uri-A")
    independent = system.create_db("uri-B")  # 相同参数仍创建独立实例(§2-3)

    g = GraphDefinition()
    for nid, ref in (("a", shared), ("b", shared), ("c", independent)):
        g.add_node(nid, "DbQuery")
        g.bind_asset(node_id=nid, slot="db", asset_id=ref.asset_id)
    report = GraphInstance.build(g, {"DbQuery": DbQuery.TYPE}, asset_resolver=system)
    assert report.ok, report.errors
    world = report.instance
    for nid in ("a", "b", "c"):
        world.run([Injection(nid, "query.sql", SLOT_DATA, Kind.DATA, "S")])
    produced = [e.payload[0] for e in world.timeline.events.values() if e.producer in ("a", "b", "c")]
    assert produced[0].startswith("db-1:") and produced[1].startswith("db-1:")  # 同引用 = 同实例
    assert produced[2].startswith("db-2:")  # 独立引用 = 独立实例


def test_null_asset_downgrade_is_real_capability():
    system = FakeAssetSystem()
    null_ref = system.create_null_db()
    report = _build(DbQuery.TYPE, ref=null_ref, resolver=system)
    assert report.ok, report.errors
    world = report.instance
    world.run([Injection("n", "query.sql", SLOT_DATA, Kind.DATA, "S")])
    assert [e.payload for e in world.timeline.events.values() if e.producer == "n"] == [[]]


# ==================================================================== 运行期断线与恢复
def test_runtime_failure_records_kind_error_then_recovers():
    system = FakeAssetSystem()
    ref = system.create_db("uri")
    report = _build(DbQuery.TYPE, ref=ref, resolver=system)
    assert report.ok, report.errors
    world = report.instance

    system.fail(ref.asset_id)  # 只有资产系统能置位断线
    world.run([Injection("n", "query.sql", SLOT_DATA, Kind.DATA, "S")])
    assert any("ConnectionError" in e.message for e in world.timeline.entries if e.kind == KIND_ERROR)

    system.recover(ref.asset_id)
    world.run([Injection("n", "query.sql", SLOT_DATA, Kind.DATA, "S")])
    assert [e.payload for e in world.timeline.events.values() if e.producer == "n"] == [["db-1:S"]]


# ==================================================================== state 拷贝纪律
def test_state_rejects_non_copyable_capability():
    class Resolver:
        def __init__(self):
            self.db = LockedDatabase()

        def resolve(self, ref):
            return self.db

    report = _build(DbRemember.TYPE, ref=AssetRef("locked"), resolver=Resolver())
    assert report.ok, report.errors
    world = report.instance
    world.run([Injection("n", "grab.x", SLOT_DATA, Kind.DATA, 1)])
    assert any("non-copyable state" in e.message for e in world.timeline.entries if e.kind == KIND_ERROR)


# ==================================================================== 绑定唯一性与构建路径
def test_duplicate_binding_raises():
    g = GraphDefinition()
    g.add_node("n", "DbQuery")
    g.bind_asset(node_id="n", slot="db", asset_id="x")
    with pytest.raises(ValueError, match="duplicate asset binding"):
        g.bind_asset(node_id="n", slot="db", asset_id="y")


def test_direct_construction_rejected():
    with pytest.raises(TypeError, match="build"):
        GraphInstance(None, None)
