"""Built-in nodes expressed in the Node Definition Language (DSL v2).

Same package standing as any external node package: this module only produces
NodeType values via the DSL front-end; the kernel never imports it.
See docs/graph-node-definition-dsl.md for the language semantics.
"""
from typing import Annotated

from eidolon_dsl import (
    AppendMarker,
    Config,
    GatedMarker,
    NodeDefinition,
    SignalMarker,
    StateMarker,
    TriggerMarker,
    group,
)


# ---- 源节点 ------------------------------------------------------------------

class Source(NodeDefinition):
    count: Annotated[int, StateMarker()] = 0

    @group(defaults={"step": 1})
    def tick(this, trigger: Annotated[bool, TriggerMarker()], cfg: Config) -> int:
        count = this.count  # type: ignore[attr-defined]
        this.count = count + cfg["step"]  # type: ignore[attr-defined]
        return count


class Constant(NodeDefinition):
    @group(defaults={"value": 0})
    def tick(trigger: Annotated[bool, TriggerMarker()], cfg: Config) -> int:
        return cfg["value"]


# ---- 数据节点 ----------------------------------------------------------------

class Sink(NodeDefinition):
    last: Annotated[int | None, StateMarker()] = None

    @group
    def consume(this, value: int) -> None:
        this.last = value  # type: ignore[attr-defined]


class Probe(NodeDefinition):
    log: Annotated[list[int], StateMarker()] = []

    @group
    def observe(this, value: int) -> None:
        this.log.append(value)  # type: ignore[attr-defined]


class Buffer(NodeDefinition):
    items: Annotated[list, StateMarker()] = []

    @group
    def put(this, item: Annotated[list[int], AppendMarker()]) -> None:
        this.items.extend(item)  # type: ignore[attr-defined]

    @group
    def flush(this, trigger: Annotated[bool, TriggerMarker()]) -> list:
        items = this.items  # type: ignore[attr-defined]
        this.items = []  # type: ignore[attr-defined]
        return items if items else None  # type: ignore[return-value]


class Join(NodeDefinition):
    @group
    def join(a: int, b: int) -> tuple[int, int]:
        return (a, b)


class Split(NodeDefinition):
    @group(outputs=("out1", "out2"))
    def fan(value: int) -> dict:
        return {"out1": value, "out2": value}


# ---- 信号节点 ----------------------------------------------------------------

class Latch(NodeDefinition):
    @group
    def release(
        gate: Annotated[bool, SignalMarker()],
        trigger: Annotated[bool, TriggerMarker()],
        data: Annotated[int, GatedMarker("gate")],
    ) -> int:
        return data


class DataToSignal(NodeDefinition):
    @group(defaults={"mode": "truthy", "threshold": 0})
    def convert(cfg: Config, data: int) -> Annotated[bool, SignalMarker()]:
        mode, threshold = cfg["mode"], cfg["threshold"]
        if mode == "truthy":
            return bool(data)
        if mode == "gt":
            return data > threshold
        if mode == "lt":
            return data < threshold
        if mode == "eq":
            return data == threshold
        raise ValueError(f"unknown mode {mode!r}")


class SignalToData(NodeDefinition):
    @group(trigger="pass")
    def pass_value(
        gate: Annotated[bool, SignalMarker()],
        x: Annotated[int, GatedMarker("gate")],
    ) -> int:
        return x


PRIMITIVE_DEFINITIONS = (Source, Constant, Sink, Probe, Buffer, Join, Split, Latch, DataToSignal, SignalToData)
PRIMITIVES = {definition.TYPE.name: definition.TYPE for definition in PRIMITIVE_DEFINITIONS}
