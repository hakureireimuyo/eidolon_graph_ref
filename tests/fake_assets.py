"""假资产系统与假能力对象：验证 Asset 层边界（graph-assets.md §9）。

真实资产系统尚未实现；这里提供最小假实现，让 Asset 层语义可被测试驱动。

设计要点（与 §2 六条原则一致）：
- FakeAssetSystem 是资产的唯一所有者：创建/失效/恢复/销毁都在这里
- 节点只通过 Capability 接口（DatabaseCapability / CacheCapability）使用资产
  ——接口不含 close() 等管理操作（§2-5：构建期类型检查即不变量执行点）
- 节点只持有引用，不创建资产、不管理生命周期（最小接触）
- FakeDatabase / FakeCache 是资产实例：可被"失效"模拟运行期断线
- 声明即必须（§7 裁定）：降级需求由资产系统提供 Null 资产（真实
  Capability，如 FakeNullDatabase）——内核永不出 None 槽位
"""

from __future__ import annotations

import threading
from typing import Protocol, runtime_checkable

from eidolon_graph_ref.model.assets import AssetRef


@runtime_checkable
class DatabaseCapability(Protocol):
    """数据库能力接口：节点唯一可见的资产表面（不含 close 等管理操作）。"""

    def query(self, sql: str) -> list: ...


@runtime_checkable
class CacheCapability(Protocol):
    """缓存能力接口（类型错误用例的另一能力种类）。"""

    def get(self, key: str) -> object: ...


class FakeDatabase:
    """假数据库资产实例。由 FakeAssetSystem 拥有；节点只按 DatabaseCapability 使用。"""

    def __init__(self, asset_id: str, uri: str):
        self.asset_id = asset_id
        self.uri = uri
        self.failed = False  # 模拟运行期断线（只有资产系统能置位）
        self.closed = False  # 只有资产系统能置位（内核没有任何调用路径）
        self.calls: list[str] = []

    def query(self, sql: str) -> list:
        if self.failed:
            raise ConnectionError(f"database {self.asset_id} is down")
        self.calls.append(sql)
        # 载荷带实例身份——共享/独立由此可辨
        return [f"{self.asset_id}:{sql}"]


class LockedDatabase:
    """带不可深拷贝属性（锁）的假能力：探测值域校验（deepcopy 判据）。

    满足 DatabaseCapability 协议（有 query），但深拷贝必然失败——
    模拟真实能力对象（连接 / 锁 / 线程不可序列化）。
    """

    def __init__(self):
        self._lock = threading.Lock()

    def query(self, sql: str) -> list:
        return [sql]


class FakeNullDatabase:
    """降级资产：无真实后端时的真实 Capability（声明即必须的降级模式）。

    节点代码与正常资产完全同构——降级策略集中在资产系统。
    """

    def __init__(self, asset_id: str):
        self.asset_id = asset_id
        self.failed = False
        self.closed = False

    def query(self, sql: str) -> list:
        if self.failed:
            raise ConnectionError(f"database {self.asset_id} is down")
        return []


class FakeCache:
    """假缓存资产实例（类型错误用例）。"""

    def __init__(self, asset_id: str, uri: str):
        self.asset_id = asset_id
        self.failed = False
        self.closed = False
        self._store: dict[str, object] = {}

    def get(self, key: str) -> object:
        if self.failed:
            raise ConnectionError(f"cache {self.asset_id} is down")
        return self._store.get(key)


class FakeAssetSystem:
    """假资产系统：资产的唯一所有者（创建/失效/恢复/销毁）。"""

    def __init__(self) -> None:
        self._assets: dict[str, object] = {}
        self._next_id = 0

    # ---- 创建（§2-3：相同参数可以创建任意多个独立实例，身份独立于参数） ----
    def create_db(self, uri: str) -> AssetRef:
        self._next_id += 1
        asset_id = f"db-{self._next_id}"
        self._assets[asset_id] = FakeDatabase(asset_id=asset_id, uri=uri)
        return AssetRef(asset_id)

    def create_cache(self, uri: str) -> AssetRef:
        self._next_id += 1
        asset_id = f"cache-{self._next_id}"
        self._assets[asset_id] = FakeCache(asset_id=asset_id, uri=uri)
        return AssetRef(asset_id)

    def create_null_db(self) -> AssetRef:
        """降级资产：无真实后端环境下的真实 Capability（声明即必须的降级模式）。"""
        self._next_id += 1
        asset_id = f"nulldb-{self._next_id}"
        self._assets[asset_id] = FakeNullDatabase(asset_id=asset_id)
        return AssetRef(asset_id)

    # ---- 资产系统的客户端视角（GraphInstance.build 的 asset_resolver） ----
    def resolve(self, ref: AssetRef):
        """ref → 实例。目录查询失败（未知 asset_id）→ KeyError。"""
        return self._assets[ref.asset_id]

    # ---- 生命周期（只有资产系统能触碰） ----
    def fail(self, asset_id: str) -> None:
        """模拟运行期断线：tick 内调用失败，资产系统后台负责重连/替换。"""
        self._assets[asset_id].failed = True

    def recover(self, asset_id: str) -> None:
        self._assets[asset_id].failed = False

    def destroy(self, asset_id: str) -> None:
        self._assets[asset_id].closed = True
        del self._assets[asset_id]

    # ---- 测试观测口 ----
    def instance(self, asset_id: str):
        return self._assets[asset_id]
