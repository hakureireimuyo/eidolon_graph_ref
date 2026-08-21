"""外部节点包端到端:一个完全外部定义的节点接入原语图(graph-node-protocol.md)。

演示节点协议的完整契约(§2-§8):外部包直接构造 NodeType 声明端口/输入组/
资产依赖(§2),init 从合并配置 + 已解析资产计算初始状态(§7),tick 经
ctx.assets 使用能力(§4),产出事件沿既有连线传播(§5);宿主负责注册
(types 字典,§8)与资产解析(AssetResolver)。内核不认识"外部"二字——
它只看见一个 NodeType 值。

拓扑:
    const(Constant) ──→ wc.text(WordCount)
    wc.count ──┬──→ probe.in       (观察记录)
               └──→ sink.in        (吸收)

WordCount:数据端口 text + 输出 count + 资产槽 dict(DictCapability);
init 用 bootstrap 配置热身能力得到 baseline;tick 计数并累加 baseline。

运行:uv run python examples/external_node.py
"""

import sys
from pathlib import Path
from typing import Protocol, runtime_checkable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eidolon_graph_ref.console import render_epoch, render_event_archive, render_state
from eidolon_graph_ref.engine.instance import GraphInstance
from eidolon_graph_ref.engine.protocol import TickOutput
from eidolon_graph_ref.model.assets import AssetIn, AssetRef
from eidolon_graph_ref.model.graph import GraphDefinition
from eidolon_graph_ref.model.node_type import InputGroup, NodeType, Policy
from eidolon_graph_ref.model.ports import DataIn, DataOut
from eidolon_graph_ref.model.validate import ensure_valid
from eidolon_primitives import PRIMITIVES


# ==================================================================== 外部节点包
# 以下全部代码属于"外部世界":只有 model/engine 公开声明与协议类型可见,
# 没有内核内部工厂、没有注册表、没有 import 钩子。


@runtime_checkable
class DictCapability(Protocol):
    """词统计能力接口(使用面):节点唯一可见的资产表面(§4)。"""

    def count(self, text: str) -> int: ...


class WordCounter:
    """假资产实例:由宿主资产系统拥有;节点只按 DictCapability 使用。"""

    def __init__(self, asset_id: str):
        self.asset_id = asset_id

    def count(self, text: str) -> int:
        return len(text.split())


def _init(ctx) -> dict | None:
    # §7:构建期初始化钩子——config = 合并配置,bootstrap 来自图上的 spec.config;
    # assets = 本节点已解析能力表(声明即必须,恒非 None)。返回初始状态增量。
    baseline = ctx.assets["dict"].count(ctx.config["bootstrap"])
    return {"baseline": baseline}


def _tick(ctx) -> TickOutput:
    # §3-§5:一次 Activation 内读本组输入、用能力、写输出与状态;
    # 不写即不投递;输出只经 TickOutput 返回内核。
    n = ctx.assets["dict"].count(ctx.data_in["text"]) + ctx.state["baseline"]
    return TickOutput(data_out={"count": n}, state={"runs": ctx.state["runs"] + 1})


WordCount = NodeType(
    name="WordCount",
    data_in=(DataIn("text"),),
    data_out=(DataOut("count"),),
    asset_in=(AssetIn("dict", DictCapability),),
    state_defaults={"baseline": 0, "runs": 0},
    config_defaults={"bootstrap": ""},
    groups=(InputGroup("count", inputs=("text",), policy=Policy.ON_ANY_DATA),),
    tick=_tick,
    init=_init,
)


# ==================================================================== 宿主侧
class DemoAssetSystem:
    """宿主资产系统的最小演示:创建/解析(§4,§8)。"""

    def __init__(self) -> None:
        self._assets: dict[str, WordCounter] = {}

    def create_counter(self, asset_id: str) -> AssetRef:
        self._assets[asset_id] = WordCounter(asset_id)
        return AssetRef(asset_id)

    def resolve(self, ref: AssetRef) -> WordCounter:
        return self._assets[ref.asset_id]


TYPES = {**PRIMITIVES, "WordCount": WordCount}  # §8:宿主决定类型全集,内核 registry-agnostic


def build() -> GraphDefinition:
    g = GraphDefinition("external-word-count")
    g.add_node("const", "Constant", value="eidolon graph kernel node protocol")
    g.add_node("wc", "WordCount", bootstrap="frozen semantics")
    g.add_node("probe", "Probe")
    g.add_node("sink", "Sink")
    g.wire("const", "out", "wc", "text")
    g.wire("wc", "count", "probe", "in")
    g.wire("wc", "count", "sink", "in")
    g.bind_asset("wc", "dict", "counter-1")
    return g


def main() -> None:
    assets = DemoAssetSystem()
    assets.create_counter("counter-1")

    g = build()
    ensure_valid(g, TYPES)  # 连线合法性校验
    result = GraphInstance.build(g, TYPES, asset_resolver=assets)
    if not result.ok:
        raise RuntimeError(result.errors)
    world = result.instance

    def show(note: str) -> None:
        run = world.run_no
        print(render_epoch(world.timeline, run))
        print()
        print(note)

    # 三个 epoch:const 每轮播种 → wc 激活计数 → probe/sink 吸收
    world.run()
    show("epoch 1: const 供给文本 → wc 计数(含 init baseline)→ 扇出吸收")
    world.run()
    show("epoch 2: 同上,再次激活")
    world.run()
    show("epoch 3: 同上,再次激活")

    print(render_event_archive(world.timeline))
    print()
    print(render_state(world.observable_state()))


if __name__ == "__main__":
    main()
