"""GraphInstance：图定义 + 运行态（世界 = 状态机/程序：注入 → 传播至静止）。

依据：graph-execution-model.md §1/§4/§5
- 规则（图定义）与事实（节点状态/端口状态）分离；运行期拓扑不可变
- 节点状态是单写者：执行时读取（深拷贝），产出新状态直接提交
- 静态/动态 = 同一端口的两种运行模式：连接状态决定输入语义，
  由内核吸收进 Readiness 计算（节点实现不感知）
- 静态值 = 配置覆盖(按端口名)或端口声明默认；动态初始 =「尚未收到事件」
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol

from ..model.assets import AssetRef
from ..model.graph import GraphDefinition, SLOT_DATA, SLOT_QUAL, SLOT_SIGNAL, SLOT_TRIGGER, Wire
from ..model.node_type import NodeType
from ..model.ports import APPEND
from .event import Injection
from .executor import Executor
from .port_state import DataPortState, SignalPortState, TriggerPortState
from .protocol import InitContext
from .timeline import Timeline


class AssetResolver(Protocol):
    """宿主传入的资产解析函数（资产系统的客户端，graph-assets.md §8）。

    资产在 Graph 构建前已由资产系统创建，构建期 lookup 即 eager（§7 裁定）。
    """

    def resolve(self, ref: AssetRef) -> Any: ...


@dataclass(frozen=True)
class BuildReport:
    """构建结果：一次性收集全部错误（资产依赖多，逐个报错会让宿主反复启动）。

    ok=False 时不存在 GraphInstance（§7 裁定：禁止"构造半成品再 try resolve"）。
    """

    ok: bool
    errors: tuple[str, ...]
    instance: "GraphInstance | None" = None


class GraphInstance:
    """一张图的运行实例：事实（节点状态 + 端口状态 + 时间线/事件档案）。

    唯一构建入口是 `GraphInstance.build()`：结构校验 → 资产解析 → 类型验证
    全部完成后才产生实例（graph-assets.md §6-7：禁止"构造半成品再 try
    resolve"）。直接构造绕过全部前置条件，是类型合法但语义非法的实例——
    内核在构造器层面拒绝。
    """

    def __init__(
        self,
        definition: GraphDefinition,
        types: dict[str, NodeType],
        assets: dict[str, dict[str, Any]] | None = None,
        init_states: dict[str, dict[str, Any]] | None = None,
        *,
        _internal: bool = False,
    ):
        if not _internal:
            raise TypeError(
                "GraphInstance 必须经 GraphInstance.build() 构建：直接构造绕过"
                "结构校验与资产解析，违反'声明即必须'（graph-assets.md §6）"
            )
        self.definition = definition
        self.types = types
        self.run_no = 0  # 已完成的 epoch 数
        self.timeline = Timeline()
        self.log: list[str] = []  # 错误日志（独立于时间线：程序打印了什么）

        # 节点事实
        self.node_states: dict[str, dict[str, Any]] = {}
        self.configs: dict[str, dict[str, Any]] = {}
        # 资产 store：与 node_states 分离，不 deepcopy（能力对象不可序列化）；
        # 快照/回放天然排除资产（graph-assets.md §8 实现要点）。
        # 键集合由 NodeType.asset_in 声明决定；由 GraphInstance.build 注入。
        self.assets: dict[str, dict[str, Any]] = {
            nid: dict(per_node) for nid, per_node in (assets or {}).items()
        }
        # init 修订后的初始状态(graph-node-protocol.md §7)：仅构建期产生，_build 按节点合入
        self._init_states: dict[str, dict[str, Any]] = dict(init_states or {})
        # 端口状态（按 (节点, 端口名) 索引；只有"已连接"的动态端口才创建 Signal 状态）
        self.data_states: dict[str, dict[str, DataPortState]] = {}
        self.qual_states: dict[str, dict[str, SignalPortState]] = {}  # 资格槽
        self.trigger_states: dict[str, dict[str, TriggerPortState]] = {}
        self.enable_states: dict[str, dict[str, SignalPortState]] = {}  # enable(节点级资格)

        # 连线索引
        self.in_index: dict[tuple[str, str, str], Wire] = {}
        self.out_index: dict[tuple[str, str], list[Wire]] = {}

        self._build()

    # ================================================================== 构建
    @classmethod
    def build(
        cls,
        definition: GraphDefinition,
        types: dict[str, NodeType],
        asset_resolver: AssetResolver | None = None,
    ) -> BuildReport:
        """构建运行实例：结构校验 → 资产解析 → 类型验证 → 注入。

        错误一次性收集进 BuildReport；ok=False 时 instance is None。
        资产解析在构建期完成（eager）：逐节点按声明序 lookup → 类型校验 →
        注入 assets store（graph-assets.md §6-8）。
        """
        from ..model.validate import validate

        errors: list[str] = []
        assets: dict[str, dict[str, Any]] = {}

        # 1. 结构校验（编辑期：节点/连线/绑定引用已声明的槽）
        errors.extend(validate(definition, types).errors)

        # 1.5 config 值域探针（编辑与运行分离：图定义是纯描述，config 也是
        # Value，不得经配置携带活对象进入运行平面）
        for nid, spec in definition.nodes.items():
            ntype = types.get(spec.type)
            if ntype is None:
                continue  # 结构校验已报错
            for key, value in {**ntype.config_defaults, **spec.config}.items():
                try:
                    deepcopy(value)
                except Exception:
                    errors.append(
                        f"node {nid!r}: config field {key!r} carries non-copyable value: {type(value).__name__}"
                    )
        if errors:
            return BuildReport(ok=False, errors=tuple(errors))

        # 2. 资产解析（运行期：目录在资产系统里）
        for nid in definition.node_order():
            ntype = types.get(definition.nodes[nid].type)
            if ntype is None:
                continue  # 结构校验已报错
            for slot in ntype.asset_in:
                ref = definition.asset_bindings.get((nid, slot.name))
                if ref is None:
                    # 声明即必须（§7 裁定）：资产是资源而非数据，缺席是结构缺陷，
                    # 构建期拦下，不允许"运行期才发现"。
                    errors.append(f"node {nid!r}: asset slot {slot.name!r} is not bound")
                    continue
                if asset_resolver is None:
                    errors.append(f"node {nid!r}: asset {ref.asset_id!r} (slot {slot.name!r}): no asset resolver")
                    continue
                try:
                    capability = asset_resolver.resolve(ref)
                except Exception as exc:
                    errors.append(
                        f"node {nid!r}: asset {ref.asset_id!r} (slot {slot.name!r}) resolve failed: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    continue
                if capability is None:
                    errors.append(f"node {nid!r}: asset {ref.asset_id!r} (slot {slot.name!r}) not found")
                    continue
                if slot.type is not None:
                    try:
                        type_ok = isinstance(capability, slot.type)
                    except TypeError:
                        errors.append(
                            f"node {nid!r}: asset {ref.asset_id!r} (slot {slot.name!r}): "
                            f"declared type {slot.type!r} is not runtime-checkable"
                        )
                        continue
                    if not type_ok:
                        errors.append(
                            f"node {nid!r}: asset {ref.asset_id!r} (slot {slot.name!r}) type mismatch: "
                            f"expected {slot.type.__name__}, got {type(capability).__name__}"
                        )
                        continue
                assets.setdefault(nid, {})[slot.name] = capability

        if errors:
            return BuildReport(ok=False, errors=tuple(errors))

        # 2.5 init 构建期初始化钩子(2026-08-21 裁定修订,graph-node-protocol.md §7)
        # 资产解析后、实例构造前,每节点至多一次;初始状态增量合并于
        # state_defaults。默认 None = 无行为变化。失败 = 结构前提失败 →
        # BuildReport error(与执行期 KIND_ERROR 分层)。
        init_states: dict[str, dict[str, Any]] = {}
        for nid in definition.node_order():
            ntype = types.get(definition.nodes[nid].type)
            if ntype is None or ntype.init is None:
                continue
            spec = definition.nodes[nid]
            merged_config = {**ntype.config_defaults, **spec.config}  # 与 _build 的 configs 同源
            ictx = InitContext(config=merged_config, assets=dict(assets.get(nid, {})))
            try:
                delta = ntype.init(ictx)
            except Exception as exc:
                errors.append(f"node {nid!r}: init raised {type(exc).__name__}: {exc}")
                continue
            if delta is None:
                continue  # 无增量:状态 = state_defaults(默认路径)
            unknown = set(delta) - set(ntype.state_defaults)
            if unknown:
                errors.append(f"node {nid!r}: init wrote undeclared state fields: {sorted(unknown)}")
                continue
            invalid: list[str] = []
            for field, value in delta.items():
                try:
                    deepcopy(value)  # 值域探针:与 config 探针同判据
                except Exception:
                    invalid.append(field)
            if invalid:
                errors.append(f"node {nid!r}: init wrote non-copyable values to state fields: {sorted(invalid)}")
                continue
            init_states[nid] = {**deepcopy(ntype.state_defaults), **deepcopy(delta)}

        if errors:
            return BuildReport(ok=False, errors=tuple(errors))
        return BuildReport(
            ok=True, errors=(), instance=cls(definition, types, assets, init_states, _internal=True)
        )

    def _build(self) -> None:
        from ..model.validate import resolve_slots

        resolve_slots(self.definition, self.types)  # 连线槽位按端口声明自动推断
        for wire in self.definition.wires:
            self.in_index[(wire.dst_node, wire.dst_port, wire.dst_slot)] = wire
            self.out_index.setdefault((wire.src_node, wire.src_port), []).append(wire)

        for nid, spec in self.definition.nodes.items():
            ntype = self.types[spec.type]
            self.configs[nid] = {**ntype.config_defaults, **spec.config}
            self.node_states[nid] = (
                deepcopy(self._init_states[nid])
                if nid in self._init_states
                else deepcopy(ntype.state_defaults)
            )

            data_states: dict[str, DataPortState] = {}
            qual_states: dict[str, SignalPortState] = {}
            for p in ntype.data_in:
                wired = (nid, p.name, SLOT_DATA) in self.in_index
                # 静态模式：配置覆盖(按端口名)或声明默认；动态模式：尚未收到事件
                static_value = self.configs[nid].get(p.name, p.default)
                if p.cache == APPEND and static_value is None:
                    static_value = []  # Append 端口的静态空态是空列表，而非 None
                data_states[p.name] = DataPortState(
                    cache=p.cache,
                    value=static_value,
                    has_value=not wired,
                    event_driven=wired,
                )
                if (nid, p.name, SLOT_QUAL) in self.in_index:
                    qual_states[p.name] = SignalPortState()
            self.data_states[nid] = data_states
            self.qual_states[nid] = qual_states
            self.trigger_states[nid] = {t.name: TriggerPortState() for t in ntype.trigger_in}
            self.enable_states[nid] = {
                s.name: SignalPortState() for s in ntype.signal_in if (nid, s.name, SLOT_SIGNAL) in self.in_index
            }

    # ================================================================== 运行
    def run(self, injections: list[Injection] | None = None) -> None:
        """推进一个 epoch（注入 → 传播至静止）。"""
        Executor(self.types).run(self, injections or [])

    # ================================================================== 观察
    def observable_state(self) -> dict:
        """全量可观察状态：节点 state 字段 + 端口状态（编辑器/控制台/测试）。"""
        result: dict[str, Any] = {}
        for nid in self.definition.node_order():
            spec = self.definition.nodes[nid]
            node_view: dict[str, Any] = {
                "type": spec.type,
                "state": deepcopy(self.node_states[nid]),
                "config": dict(self.configs[nid]),
                "data_in": {
                    name: {
                        "value": st.value,
                        "has_value": st.has_value,
                        "pending": st.pending,
                        "qual": (
                            {"level": qs.level, "pending": qs.pending}
                            if (qs := self.qual_states[nid].get(name)) is not None
                            else None
                        ),
                    }
                    for name, st in self.data_states[nid].items()
                },
                "trigger_in": {
                    name: {"pending": st.pending, "payload": st.payload if st.has_payload else None}
                    for name, st in self.trigger_states[nid].items()
                },
                "enable": {
                    name: {"level": st.level, "pending": st.pending}
                    for name, st in self.enable_states[nid].items()
                },
                # 资产只暴露结构事实（ref/resolved），绝不暴露对象（§8）
                "assets": {
                    a.name: {
                        "ref": (
                            ref.asset_id
                            if (ref := self.definition.asset_bindings.get((nid, a.name))) is not None
                            else None
                        ),
                        "resolved": self.assets.get(nid, {}).get(a.name) is not None,
                    }
                    for a in self.types[spec.type].asset_in
                },
            }
            result[nid] = node_view
        return result
