"""节点类型声明与输入组。

依据：node-protocol.md §2 / graph-node-types.md §2 / graph-ports-bindings.md §2.4

节点 = 类实例；输入组 = 方法；输出组 = 方法的返回值。
组触发 = Readiness 检查（InputGroup.policy 描述 pending 如何聚合为 Readiness）。
静态端口不计入数据条件（全静态 = 数据条件真空为真）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .assets import AssetIn
from .ports import DataIn, DataOut, SignalIn, SignalOut, TriggerIn


class Policy(str, Enum):
    """组触发策略：pending 如何聚合为 Readiness（graph-ports-bindings.md §2.4）。"""

    ON_ALL_DATA_READY = "on_all_data_ready"  # 组内全部动态 Data 端口 pending（默认）
    ON_ANY_DATA = "on_any_data"  # 任一动态 Data 端口 pending
    ON_TRIGGER = "on_trigger"  # 任一 TriggerIn pending（纯事件节点）
    ON_DATA_AND_TRIGGER = "on_data_and_trigger"  # 数据齐 pending + Trigger pending（显式门控执行）


@dataclass(frozen=True)
class InputGroup:
    """输入组 = 函数调用：inputs(数据参数) + triggers(触发入口) + policy(触发策略)。"""

    name: str
    inputs: tuple[str, ...] = ()  # DataIn 端口名
    triggers: tuple[str, ...] = ()  # TriggerIn 端口名
    policy: Policy = Policy.ON_ALL_DATA_READY


@dataclass(frozen=True)
class NodeType:
    """节点类型声明（声明 = 规则，实现 = 代码，二者分离）。

    - state_defaults：状态字段表（带默认值）——实例跨轮事实的唯一存储
    - config_defaults：配置字段表（编辑期覆盖，运行时只读）
    - groups：输入组 = 函数；每组执行时只读本组输入，组间数据经节点状态传递
    - asset_in：资产依赖声明（资源平面）；运行时经 ctx.assets[槽名] 使用——
      只有使用权，没有所有权（graph-assets.md §2-5）
    - tick：各组处理逻辑（运行时唯一可重载点）；Readiness 判定、pending 消费、
      输出投递、状态提交是基类 final 语义，节点不可触碰
    - init：构建期初始化钩子（graph-node-protocol.md §7，2026-08-21 裁定修订）：
      init(ctx) -> dict | None，资产解析后、实例构造前调用一次；返回初始
      状态增量合并于 state_defaults；无运行时事件语义
    """

    name: str
    data_in: tuple[DataIn, ...] = ()
    data_out: tuple[DataOut, ...] = ()
    trigger_in: tuple[TriggerIn, ...] = ()
    signal_in: tuple[SignalIn, ...] = ()  # enable（节点级资格）
    signal_out: tuple[SignalOut, ...] = ()  # 与 data_out 自由组合(2026-08-21 修订:无类别约束,写必须声明)
    asset_in: tuple[AssetIn, ...] = ()  # 资产依赖声明(资源平面,与数据/触发/信号并列)
    state_defaults: dict[str, Any] = field(default_factory=dict)
    config_defaults: dict[str, Any] = field(default_factory=dict)
    groups: tuple[InputGroup, ...] = ()
    tick: Any = None  # tick(ctx) -> TickOutput（实现绑定，Python 函数/方法）
    init: Any = None  # init(ctx: InitContext) -> dict | None（构建期初始化钩子，§7 裁定修订）

    # ---- 派生判定 ---------------------------------------------------------
    @property
    def is_source(self) -> bool:
        """源节点：无输入组。每 epoch 按声明序播种执行一次（group="step"）。"""
        return len(self.groups) == 0

    @property
    def is_signal_node(self) -> bool:
        """派生观察：声明了 SignalOut(可观察面/可视化用,不参与执行约束;2026-08-21 修订)。"""
        return len(self.signal_out) > 0

    # ---- 查询 -------------------------------------------------------------
    def port(self, name: str):
        """按名查找输入端口声明（DataIn/TriggerIn/SignalIn）。"""
        for p in (*self.data_in, *self.trigger_in, *self.signal_in):
            if p.name == name:
                return p
        raise KeyError(f"node type {self.name!r} has no input port {name!r}")

    def out_port(self, name: str):
        for p in (*self.data_out, *self.signal_out):
            if p.name == name:
                return p
        raise KeyError(f"node type {self.name!r} has no output port {name!r}")

    def group(self, name: str) -> InputGroup:
        for g in self.groups:
            if g.name == name:
                return g
        raise KeyError(f"node type {self.name!r} has no group {name!r}")

    def to_dict(self) -> dict:
        """声明的可观察形态（后续可视化/编辑器的基础）。"""
        return {
            "name": self.name,
            "data_in": [
                {"name": p.name, "default": p.default, "cache": p.cache, "qualified": p.qualified}
                for p in self.data_in
            ],
            "data_out": [p.name for p in self.data_out],
            "trigger_in": [p.name for p in self.trigger_in],
            "signal_in": [p.name for p in self.signal_in],
            "signal_out": [p.name for p in self.signal_out],
            "asset_in": [
                {"name": p.name, "type": p.type.__name__ if p.type else None}
                for p in self.asset_in
            ],
            "state_defaults": dict(self.state_defaults),
            "config_defaults": dict(self.config_defaults),
            "groups": [
                {"name": g.name, "inputs": list(g.inputs), "triggers": list(g.triggers), "policy": g.policy.value}
                for g in self.groups
            ],
            "has_init": self.init is not None,
            "is_source": self.is_source,
            "is_signal_node": self.is_signal_node,
        }
