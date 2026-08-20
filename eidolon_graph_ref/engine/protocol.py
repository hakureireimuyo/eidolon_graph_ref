"""节点 ABI：内核 ↔ 节点实现。

依据：node-protocol.md §3
- tick(ctx) -> TickOutput：一个输入组执行一次；源节点自走执行 group="step"
- ctx.data_in 只含本组已解析输入（资格关闭的端口以默认属性参与）
- ctx.state 为当前状态深拷贝；ctx.config 只读
- TickOutput.data_out：不写即不投递（没有隐式输出信号）
- TickOutput.signal_out：仅信号节点可写（数据节点触碰 = 声明违规）
- Readiness 判定、pending 消费、输出投递、状态提交是基类 final 语义，
  节点只重载各组处理逻辑
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TickContext:
    group: str  # 组名（源节点自走执行为 "step"）
    data_in: dict[str, Any]  # 本组已解析输入（Data 端口 effective 值 + TriggerIn 载荷）
    state: dict[str, Any]  # 当前状态深拷贝
    config: dict[str, Any]
    # 本节点资产能力表（浅拷贝：能力对象共享，tick 插入不影响节点 store）。
    # 键集合由 NodeType.asset_in 声明决定；声明即必须——构建期已全部绑定并
    # 解析成功（graph-assets.md §7 裁定），不存在 None 槽位。tick 只有使用权。
    assets: dict[str, Any] = field(default_factory=dict)  # 只读配置

    # 节点实现约定（ABI 的一部分，内核零复制投递）：
    # data_in 中的值可能与其他下游端口共享同一个 Python 对象（扇出零复制）——
    # 视为只读，禁止原地修改。任何分支的原地修改会被其他分支看到，
    # 形成隐藏通道、引入顺序相关、破坏确定性。需要保存输入时自行拷贝
    # （拷贝深度由节点决定，内核不代劳）；产出时应构造新对象。
    # state 是深拷贝（修改安全），但写入 state 的值成为世界事实——
    # 若直接存 data_in 的对象引用，同样受上述约定约束。


@dataclass
class TickOutput:
    # 值域 = Value（可复制/可序列化）：Capability 不得进入状态/传播平面
    # （2026-08-20 裁定；内核在提交与产出时校验，违者 KIND_ERROR）
    data_out: dict[str, Any] = field(default_factory=dict)  # 输出端口名 → 值（不写即不投递）
    signal_out: dict[str, bool] = field(default_factory=dict)  # 仅信号节点可写
    state: dict[str, Any] = field(default_factory=dict)  # 状态变更字段增量
