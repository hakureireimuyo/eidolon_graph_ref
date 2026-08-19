"""验证原语节点：内核语义的"关键字/基础指令"，不为业务功能。

依据：《ChatGPT-架构验证性重写-20260819-1140.md》原语清单 +
graph-port-capability-composition.md §3.7（Buffer = Base + Append 端口 + 显式 TriggerIn）

这些节点回答内核设计问题：Buffer 能否表达"数据暂存但不产生执行事件"、
Latch 能否表达"等待控制信号后释放已有数据"、Join 能否表达多输入同步关系。
如果它们必须修改 Kernel 才能实现，说明 Kernel 基础语义尚未收敛。

所有原语统一声明节点级 enable SignalIn（结构级门控验证）。
"""

from __future__ import annotations

from ..engine.protocol import TickContext, TickOutput
from ..model.node_type import InputGroup, NodeType, Policy
from ..model.ports import APPEND, REPLACE, DataIn, DataOut, SignalIn, SignalOut, TriggerIn

_ENABLE = SignalIn("enable")  # 节点级资格：LOW = 整节点不执行，数据照常缓存


def _define(
    name: str,
    *,
    data_in: tuple = (),
    data_out: tuple = (),
    trigger_in: tuple = (),
    signal_out: tuple = (),
    state_defaults: dict | None = None,
    config_defaults: dict | None = None,
    groups: tuple = (),
    tick=None,
) -> NodeType:
    return NodeType(
        name=name,
        data_in=tuple(data_in),
        data_out=tuple(data_out),
        trigger_in=tuple(trigger_in),
        signal_in=(_ENABLE,),
        signal_out=tuple(signal_out),
        state_defaults=state_defaults or {},
        config_defaults=config_defaults or {},
        groups=tuple(groups),
        tick=tick,
    )


# ==================================================================== 源节点
def source() -> NodeType:
    """源节点：每 epoch 播种执行一次(step)，产出 state.count 并自增。验证源节点播种。"""

    def tick(ctx: TickContext) -> TickOutput:
        count = ctx.state["count"]
        return TickOutput(data_out={"out": count}, state={"count": count + ctx.config["step"]})

    return _define(
        "Source",
        data_out=(DataOut("out"),),
        state_defaults={"count": 0},
        config_defaults={"step": 1},
        tick=tick,
    )


def constant() -> NodeType:
    """无状态源节点：每 epoch 播种产出配置值。"""

    def tick(ctx: TickContext) -> TickOutput:
        return TickOutput(data_out={"out": ctx.config["value"]})

    return _define(
        "Constant",
        data_out=(DataOut("out"),),
        config_defaults={"value": 0},
        tick=tick,
    )


# ==================================================================== 数据节点
def sink() -> NodeType:
    """传播终点吸收：记录最后收到的值，无输出。"""

    def tick(ctx: TickContext) -> TickOutput:
        return TickOutput(state={"last": ctx.data_in["in"]})

    return _define(
        "Sink",
        data_in=(DataIn("in"),),
        state_defaults={"last": None},
        groups=(InputGroup(name="in", inputs=("in",), policy=Policy.ON_ANY_DATA),),
        tick=tick,
    )


def probe() -> NodeType:
    """显式状态可观察点：追加记录收到的每个值，无输出。"""

    def tick(ctx: TickContext) -> TickOutput:
        return TickOutput(state={"log": [*ctx.state["log"], ctx.data_in["in"]]})

    return _define(
        "Probe",
        data_in=(DataIn("in"),),
        state_defaults={"log": []},
        groups=(InputGroup(name="in", inputs=("in",), policy=Policy.ON_ANY_DATA),),
        tick=tick,
    )


def buffer() -> NodeType:
    """Buffer = Base + Append 数据端口 + 显式 TriggerIn + 重载激活行为。

    数据到达只累积(put 组)，不产生输出；flush 触发才取出全部累积。
    与旧实现的区别：put/flush 语义由端口声明与 TriggerIn 表达，不依赖组声明序区分。
    """

    def tick(ctx: TickContext) -> TickOutput:
        if ctx.group == "put":
            # 端口累积列表即存储；state.items 是跨组可见的镜像(赋值，非追加)
            return TickOutput(state={"items": list(ctx.data_in["put"])})
        # flush：产出全部累积快照并清空；空缓冲 = 无事实发生，不产出
        items = list(ctx.state["items"])
        if not items:
            return TickOutput(state={"items": []})
        return TickOutput(data_out={"out": items}, state={"items": []})

    return _define(
        "Buffer",
        data_in=(DataIn("put", cache=APPEND),),
        data_out=(DataOut("out"),),
        trigger_in=(TriggerIn("flush"),),
        state_defaults={"items": []},
        groups=(
            InputGroup(name="put", inputs=("put",), policy=Policy.ON_ANY_DATA),
            InputGroup(name="flush", triggers=("flush",), policy=Policy.ON_TRIGGER),
        ),
        tick=tick,
    )


def join() -> NodeType:
    """多输入同步汇合：a、b 全部动态数据 pending(且资格满足)才执行，产出 tuple(a, b)。

    两个端口都声明资格槽：验证 Readiness = pending AND 资格叠加。
    """

    def tick(ctx: TickContext) -> TickOutput:
        return TickOutput(data_out={"out": (ctx.data_in["a"], ctx.data_in["b"])})

    return _define(
        "Join",
        data_in=(DataIn("a", qualified=True), DataIn("b", qualified=True)),
        data_out=(DataOut("out"),),
        groups=(InputGroup(name="sync", inputs=("a", "b"), policy=Policy.ON_ALL_DATA_READY),),
        tick=tick,
    )


def split() -> NodeType:
    """多输出发射：一次执行产出两个独立事件(out1、out2 同值)。验证多输出与扇出。"""

    def tick(ctx: TickContext) -> TickOutput:
        return TickOutput(data_out={"out1": ctx.data_in["in"], "out2": ctx.data_in["in"]})

    return _define(
        "Split",
        data_in=(DataIn("in"),),
        data_out=(DataOut("out1"), DataOut("out2")),
        groups=(InputGroup(name="fan", inputs=("in",), policy=Policy.ON_ANY_DATA),),
        tick=tick,
    )


def latch() -> NodeType:
    """受控释放：数据照常缓存(Replace + 资格槽)，release 触发到达且资格 HIGH 才产出缓存值。

    正是 D1/S1 配对核心案例（graph-port-capability-composition.md §4）的节点形态：
    Readiness = Data.pending AND Qual.pending AND level==HIGH AND Trigger.pending。
    """

    def tick(ctx: TickContext) -> TickOutput:
        return TickOutput(data_out={"out": ctx.data_in["data"]})

    return _define(
        "Latch",
        data_in=(DataIn("data", cache=REPLACE, qualified=True),),
        data_out=(DataOut("out"),),
        trigger_in=(TriggerIn("release"),),
        groups=(
            InputGroup(name="release", inputs=("data",), triggers=("release",), policy=Policy.ON_DATA_AND_TRIGGER),
        ),
        tick=tick,
    )


# ==================================================================== 信号节点
def data_to_signal() -> NodeType:
    """数据 → 信号显式转换（控制流构造）：读数据值算电平，写 SignalOut。

    信号节点 = 声明 SignalOut 的节点；数据节点永远不写信号。
    """

    def tick(ctx: TickContext) -> TickOutput:
        value = ctx.data_in["data"]
        mode = ctx.config["mode"]
        threshold = ctx.config["threshold"]
        if mode == "truthy":
            level = bool(value)
        elif mode == "gt":
            level = value > threshold
        elif mode == "lt":
            level = value < threshold
        elif mode == "eq":
            level = value == threshold
        else:
            raise ValueError(f"unknown mode {mode!r}")
        return TickOutput(signal_out={"level": level})

    return _define(
        "DataToSignal",
        data_in=(DataIn("data"),),
        signal_out=(SignalOut("level"),),
        config_defaults={"mode": "truthy", "threshold": 0},
        groups=(InputGroup(name="convert", inputs=("data",), policy=Policy.ON_ANY_DATA),),
        tick=tick,
    )


def signal_to_data() -> NodeType:
    """信号 → 数据 = 受控输入（graph-ports-bindings.md §4.7 的文档定义）。

    接线形态：一条信号源扇出到「资格槽(qual) + 触发端口(pass)」两个槽位——
    信号的两重语义分别消费：level 状态(资格)与 occurrence(激活请求)。
    pass 触发且资格 HIGH → 产出 x 的有效值(x 静态 = 受控默认参数；
    x 动态 = 受控数据流)。LOW → 不执行不产出(无事实发生，无隐式事件)。
    """

    def tick(ctx: TickContext) -> TickOutput:
        return TickOutput(data_out={"out": ctx.data_in["x"]})

    return _define(
        "SignalToData",
        data_in=(DataIn("x", qualified=True),),
        data_out=(DataOut("out"),),
        trigger_in=(TriggerIn("pass"),),
        groups=(
            InputGroup(name="pass", inputs=("x",), triggers=("pass",), policy=Policy.ON_DATA_AND_TRIGGER),
        ),
        tick=tick,
    )


PRIMITIVES: dict[str, NodeType] = {t.name: t for t in [source(), constant(), sink(), probe(), buffer(), join(), split(), latch(), data_to_signal(), signal_to_data()]}
