"""Eidolon 语义 IR(Node ABI):已解析的节点契约。

NodeType 是节点定义语言(DSL v2)的编译目标,也是内核的唯一输入形态:

- 脱离 Python 后依然成立的契约描述(冻结 dataclass,可序列化、可 to_dict),
  编辑器平面与运行时平面共享同一份 IR;
- 运行时执行的是语义,不是语法——内核只查询已解析的 inputs / triggers /
  outputs / readiness / defaults / handler,不重新理解任何声明语法;
- 任何存在于 DSL 与 NodeType 之间的东西,必须具有独立的语义职责
  (docs/graph-node-protocol.md §1.0)。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from .assets import AssetIn
from .ports import DataIn, DataOut, SignalIn, SignalOut, TriggerIn
from .readiness import Readiness


@dataclass(frozen=True)
class DocSection:
    """说明书一节(标题 + 行文本)。"""
    title: str
    lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocSpec:
    """节点说明书(描述层元数据,执行路径禁止读取;与 tags 同层)。"""
    summary: str
    sections: tuple[DocSection, ...] = ()

@dataclass(frozen=True)
class Group:
    """One independently callable node interface."""
    name: str
    inputs: tuple[str, ...] = ()
    triggers: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    defaults: dict[str, Any] = field(default_factory=dict)
    handler: Any = None
    readiness: Readiness | None = None

@dataclass(frozen=True)
class NodeType:
    """语义 IR / Node ABI:一份已完成语义解析的节点契约(身份见模块 docstring)。"""

    name: str
    data_in: tuple[DataIn, ...] = ()
    data_out: tuple[DataOut, ...] = ()
    trigger_in: tuple[TriggerIn, ...] = ()
    signal_in: tuple[SignalIn, ...] = ()
    signal_out: tuple[SignalOut, ...] = ()
    asset_in: tuple[AssetIn, ...] = ()
    state_defaults: dict[str, Any] = field(default_factory=dict)
    init_defaults: dict[str, Any] = field(default_factory=dict)
    groups: tuple[Group, ...] = ()
    tags: tuple[str, ...] = ()
    init: Any = None
    doc: DocSpec | None = None
    def __post_init__(self) -> None:
        """组间不变式:契约非法状态不可构造(校验内联进 IR,而非外部校验器)。

        端口归属唯一是 IR 的结构性要求:一个输入/触发器/输出端口至多属于
        一个组,且每个输入端口必须归属某个组。此前由 validate._type_errors
        兜底——但手工构造 NodeType(绕过 DSL)可构造出二义契约,执行语义
        未定义。约束由 IR 自身保证:任何构造路径(DSL 编译、手工构造、
        序列化恢复)都必须先通过本校验。
        """

        name = self.name

        def _err(msg: str) -> None:
            raise ValueError(f"node type {name!r}: {msg}")

        group_names = [g.name for g in self.groups]
        if len(group_names) != len(set(group_names)):
            _err("duplicate group name")
        data_ports = {p.name for p in self.data_in}
        signal_ports = {p.name for p in self.signal_in}
        trigger_ports = {p.name for p in self.trigger_in}
        out_ports = {p.name for p in (*self.data_out, *self.signal_out)}
        in_names = [p.name for p in (*self.data_in, *self.trigger_in, *self.signal_in)]
        if len(in_names) != len(set(in_names)):
            _err("duplicate input port name across port categories")
        out_names = [p.name for p in (*self.data_out, *self.signal_out)]
        if len(out_names) != len(set(out_names)):
            _err("duplicate output port name across port categories")
        bound: set[str] = set()
        for p in self.data_in:
            if p.signal is not None:
                if p.signal not in signal_ports:
                    _err(f"DataIn {p.name!r} references unknown SignalIn {p.signal!r}")
                if p.signal in bound:
                    _err(f"a SignalIn may bind only one DataIn ({p.signal!r})")
                bound.add(p.signal)
        allowed_inputs = data_ports | (signal_ports - bound)
        input_owner: dict[str, str] = {}
        trigger_owner: dict[str, str] = {}
        output_owner: dict[str, str] = {}
        for g in self.groups:
            if g.handler is None:
                _err(f"group {g.name!r}: handler is required")
            if not g.inputs and not g.triggers and g.readiness is None:
                _err(f"group {g.name!r}: empty default group")
            for p in g.inputs:
                if p not in allowed_inputs:
                    _err(f"group {g.name!r}: invalid input {p!r}")
                if p in input_owner:
                    _err(f"input {p!r} belongs to both {input_owner[p]!r} and {g.name!r}")
                input_owner[p] = g.name
            for t in g.triggers:
                if t not in trigger_ports:
                    _err(f"group {g.name!r}: invalid trigger {t!r}")
                if t in trigger_owner:
                    _err(f"trigger {t!r} belongs to both {trigger_owner[t]!r} and {g.name!r}")
                trigger_owner[t] = g.name
            for o in g.outputs:
                if o not in out_ports:
                    _err(f"group {g.name!r}: invalid output {o!r}")
                if o in output_owner:
                    _err(f"output {o!r} belongs to both {output_owner[o]!r} and {g.name!r}")
                output_owner[o] = g.name
            if g.readiness is not None and not g.readiness.referenced_ports() <= set(g.inputs) | set(g.triggers):
                _err(f"group {g.name!r}: readiness references non-group port")
        for p in allowed_inputs:
            if p not in input_owner:
                _err(f"input {p!r} is not assigned to a group")

    @property
    def is_signal_node(self) -> bool: return bool(self.signal_out)
    def port(self, name: str):
        for p in (*self.data_in, *self.trigger_in, *self.signal_in):
            if p.name == name: return p
        raise KeyError(f"node type {self.name!r} has no input port {name!r}")
    def out_port(self, name: str):
        for p in (*self.data_out, *self.signal_out):
            if p.name == name: return p
        raise KeyError(f"node type {self.name!r} has no output port {name!r}")
    def group(self, name: str) -> Group:
        for g in self.groups:
            if g.name == name: return g
        raise KeyError(f"node type {self.name!r} has no group {name!r}")
    def to_dict(self) -> dict:
        return {"name": self.name, "data_in": [p.__dict__.copy() for p in self.data_in],
                "data_out": [p.name for p in self.data_out], "trigger_in": [p.name for p in self.trigger_in],
                "signal_in": [p.name for p in self.signal_in], "signal_out": [p.name for p in self.signal_out],
                "asset_in": [{"name": p.name, "type": p.type.__name__ if p.type else None} for p in self.asset_in],
                "state_defaults": dict(self.state_defaults), "init_defaults": dict(self.init_defaults), "tags": list(self.tags),
                "groups": [{"name": g.name, "inputs": list(g.inputs), "triggers": list(g.triggers), "outputs": list(g.outputs), "defaults": dict(g.defaults), "has_handler": g.handler is not None, "readiness": repr(g.readiness)} for g in self.groups],
                "has_init": self.init is not None, "is_signal_node": self.is_signal_node,
                "doc": None if self.doc is None else {"summary": self.doc.summary,
                      "sections": [{"title": s.title, "lines": list(s.lines)} for s in self.doc.sections]}}
