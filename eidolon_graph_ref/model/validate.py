"""连线合法性校验（仅连线规则，无死等拓扑诊断——最小验证范围不包含）。

依据：graph-ports-bindings.md §4.4 连线校验表 + §4.3 扇入禁止

| 连接                    | 合法性 | 语义 |
|-------------------------|--------|------|
| DataOut → DataIn(数据槽) | ✓ | 参数绑定(数据传递) |
| DataOut → TriggerIn     | ✓ | 激活请求(载荷 + 激活) |
| SignalOut → SignalIn    | ✓ | 节点级资格(enable) |
| SignalOut → DataIn 资格槽 | ✓ | 端口级资格 |
| SignalOut → TriggerIn   | ✓ | 激活请求(每次 Signal Event 一次) |
| SignalOut → 纯 DataIn(无资格槽) | ✗ | 类型不匹配 |
| DataOut → SignalIn      | ✗ | 类型不匹配 |
| DataOut → 资格槽         | ✗ | 数据线进信号槽，类型不匹配 |
"""

from __future__ import annotations

from dataclasses import dataclass

from .graph import GraphDefinition, SLOT_DATA, SLOT_QUAL, SLOT_SIGNAL, SLOT_TRIGGER, Wire
from .node_type import NodeType
from .ports import DataIn, DataOut, SignalIn, SignalOut, TriggerIn


@dataclass(frozen=True)
class ValidationResult:
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


class ValidationError(Exception):
    """图定义非法（连线 kind 不匹配 / 扇入 / 未知引用）。"""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _check_wire(
    src_type: NodeType,
    src_port: str,
    dst_type: NodeType,
    dst_port: str,
    dst_slot: str,
) -> str | None:
    """校验单条连线的 kind 匹配，返回错误描述或 None。"""
    try:
        src = src_type.out_port(src_port)
    except KeyError:
        return f"{src_type.name} has no output port {src_port!r}"
    try:
        dst = dst_type.port(dst_port)
    except KeyError:
        return f"{dst_type.name} has no input port {dst_port!r}"

    if isinstance(src, DataOut):
        if isinstance(dst, DataIn):
            if dst_slot != SLOT_DATA:
                return f"DataOut {src_port!r} cannot wire into slot {dst_slot!r} of DataIn {dst_port!r}"
            return None
        if isinstance(dst, TriggerIn):
            if dst_slot != SLOT_TRIGGER:
                return f"DataOut {src_port!r} into TriggerIn {dst_port!r} must use slot {SLOT_TRIGGER!r}"
            return None  # 载荷 + 激活
        if isinstance(dst, SignalIn):
            return f"DataOut {src_port!r} → SignalIn {dst_port!r}: 类型不匹配"
        raise AssertionError(dst)

    if isinstance(src, SignalOut):
        if isinstance(dst, DataIn):
            if dst_slot == SLOT_QUAL:
                if not dst.qualified:
                    return f"SignalOut {src_port!r} → DataIn {dst_port!r}: 未声明资格槽，类型不匹配"
                return None  # 端口级资格
            if dst_slot is None and dst.qualified:
                return f"SignalOut {src_port!r} → DataIn {dst_port!r} 二义：请显式指定 slot(数据槽/资格槽)"
            return f"SignalOut {src_port!r} cannot wire into slot {dst_slot!r} of DataIn {dst_port!r}"
        if isinstance(dst, TriggerIn):
            if dst_slot != SLOT_TRIGGER:
                return f"SignalOut {src_port!r} into TriggerIn {dst_port!r} must use slot {SLOT_TRIGGER!r}"
            return None  # 每次 Signal Event = 一次激活请求
        if isinstance(dst, SignalIn):
            if dst_slot != SLOT_SIGNAL:
                return f"SignalOut {src_port!r} into SignalIn {dst_port!r} must use slot {SLOT_SIGNAL!r}"
            return None  # 节点级资格
        raise AssertionError(dst)

    raise AssertionError(src)


def resolve_slots(graph: GraphDefinition, types: dict[str, NodeType]) -> None:
    """就地解析未指明槽位的连线：按端口声明自动推断。

    规则：TriggerIn → trigger；SignalIn → signal；DataIn → data。
    例外：SignalOut → 已声明资格槽的 DataIn 二义（数据槽/资格槽均合法），
    必须显式指定——保留 None 由 validate 报错。
    """
    resolved: list[Wire] = []
    for w in graph.wires:
        slot = w.dst_slot
        if slot is None and w.dst_node in graph.nodes and w.src_node in graph.nodes:
            dst_type = types.get(graph.nodes[w.dst_node].type)
            src_type = types.get(graph.nodes[w.src_node].type)
            if dst_type is not None and src_type is not None:
                try:
                    dst = dst_type.port(w.dst_port)
                    src = src_type.out_port(w.src_port)
                    if isinstance(dst, TriggerIn):
                        slot = SLOT_TRIGGER
                    elif isinstance(dst, SignalIn):
                        slot = SLOT_SIGNAL
                    elif isinstance(dst, DataIn) and not (isinstance(src, SignalOut) and dst.qualified):
                        slot = SLOT_DATA
                    # 二义：SignalOut → DataIn(qualified)，保留 None
                except KeyError:
                    pass  # 未知端口/节点，由 validate 报错
        resolved.append(
            Wire(src_node=w.src_node, src_port=w.src_port, dst_node=w.dst_node, dst_port=w.dst_port, dst_slot=slot)
        )
    graph._wires = resolved


def validate(graph: GraphDefinition, types: dict[str, NodeType]) -> ValidationResult:
    """校验整张图：节点类型存在性、config 字段存在性、连线 kind、扇入禁止。

    副作用：就地解析未指明槽位的连线（resolve_slots），调用后可安全构建实例。
    """
    errors: list[str] = []
    warnings: list[str] = []
    resolve_slots(graph, types)

    for nid, spec in graph.nodes.items():
        ntype = types.get(spec.type)
        if ntype is None:
            errors.append(f"node {nid!r}: unknown node type {spec.type!r}")
            continue
        # 合法 config 键：配置字段表 ∪ 数据端口名（按端口名覆盖该端口的静态默认值——
        # "可选参数:未接线 = 静态(回退配置默认值)"，graph-ports-bindings.md §2.2）
        valid_keys = set(ntype.config_defaults) | {p.name for p in ntype.data_in}
        for key in spec.config:
            if key not in valid_keys:
                errors.append(f"node {nid!r}: unknown config field {key!r}")

    # 资产绑定：编辑期只检查结构（绑定引用已声明的槽、节点存在；绑定唯一由
    # (node, slot) 键保证）。资产是否存在是运行期问题（目录在资产系统里），
    # 编辑期不检查（graph-assets.md §8）。
    for (nid, slot), ref in graph.asset_bindings.items():
        if nid not in graph.nodes:
            errors.append(f"asset binding ({nid!r}, {slot!r}): unknown node {nid!r}")
            continue
        ntype = types.get(graph.nodes[nid].type)
        if ntype is None:
            continue  # 未知节点类型已报错
        if slot not in {a.name for a in ntype.asset_in}:
            errors.append(
                f"asset binding ({nid!r}, {slot!r}): node type {ntype.name!r} declares no asset slot {slot!r}"
            )

    # 扇入禁止：每(节点, 端口, 槽位)至多一条线
    seen: set[tuple[str, str, str]] = set()
    for wire in graph.wires:
        if wire.src_node not in graph.nodes:
            errors.append(f"wire {wire!r}: unknown src node {wire.src_node!r}")
            continue
        if wire.dst_node not in graph.nodes:
            errors.append(f"wire {wire!r}: unknown dst node {wire.dst_node!r}")
            continue
        target = (wire.dst_node, wire.dst_port, wire.dst_slot)
        if target in seen:
            errors.append(f"wire {wire!r}: fan-in forbidden on {target}")
        seen.add(target)

        src_type = types.get(graph.nodes[wire.src_node].type)
        dst_type = types.get(graph.nodes[wire.dst_node].type)
        if src_type is None or dst_type is None:
            continue
        err = _check_wire(src_type, wire.src_port, dst_type, wire.dst_port, wire.dst_slot)
        if err:
            errors.append(f"wire {wire!r}: {err}")

    return ValidationResult(errors=tuple(errors), warnings=tuple(warnings))


def ensure_valid(graph: GraphDefinition, types: dict[str, NodeType]) -> ValidationResult:
    """校验，非法则抛 ValidationError。"""
    result = validate(graph, types)
    if not result.ok:
        raise ValidationError(list(result.errors))
    return result
