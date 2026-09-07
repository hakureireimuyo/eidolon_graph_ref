"""Built-in nodes expressed in the Node Definition Language (DSL v2).

Same package standing as any external node package: this module only produces
NodeType values via the DSL front-end; the kernel never imports it.
See docs/graph-node-definition-dsl.md for the language semantics.
"""
from typing import Annotated

from eidolon_dsl import (
    AppendMarker,
    Config,
    DataEvent,
    GatedMarker,
    NodeDefinition,
    SignalEvent,
    SignalMarker,
    StateMarker,
    TriggerMarker,
    group,
)


# ---- 源节点 ------------------------------------------------------------------

class Source(NodeDefinition):
    count: Annotated[int, StateMarker()] = 0

    @group(defaults={"step": 1})
    def tick(this, trigger: Annotated[bool, TriggerMarker()], cfg: Config) -> DataEvent:
        count = this.count  # type: ignore[attr-defined]
        this.count = count + cfg["step"]  # type: ignore[attr-defined]
        return DataEvent(count=count)


class Constant(NodeDefinition):
    @staticmethod
    @group(defaults={"value": 0})
    def tick(trigger: Annotated[bool, TriggerMarker()], cfg: Config) -> DataEvent:
        return DataEvent(value=cfg["value"])


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
    def flush(this, trigger: Annotated[bool, TriggerMarker()]) -> DataEvent | None:
        items = this.items  # type: ignore[attr-defined]
        this.items = []  # type: ignore[attr-defined]
        return DataEvent(items=items) if items else None


class Join(NodeDefinition):
    @staticmethod
    @group
    def join(a: int, b: int) -> DataEvent:
        return DataEvent(pair=(a, b))


class Split(NodeDefinition):
    @staticmethod
    @group(outputs=("out1", "out2"))
    def fan(value: int) -> DataEvent:
        return DataEvent(out1=value, out2=value)


# ---- 信号节点 ----------------------------------------------------------------

class Latch(NodeDefinition):
    @staticmethod
    @group
    def release(
        gate: Annotated[bool, SignalMarker()],
        trigger: Annotated[bool, TriggerMarker()],
        data: Annotated[int, GatedMarker("gate")],
    ) -> DataEvent:
        return DataEvent(data=data)


class DataToSignal(NodeDefinition):
    @staticmethod
    @group(defaults={"mode": "truthy", "threshold": 0})
    def convert(cfg: Config, data: int) -> SignalEvent:
        mode, threshold = cfg["mode"], cfg["threshold"]
        if mode == "truthy":
            return SignalEvent(value=bool(data))
        if mode == "gt":
            return SignalEvent(value=data > threshold)
        if mode == "lt":
            return SignalEvent(value=data < threshold)
        if mode == "eq":
            return SignalEvent(value=data == threshold)
        raise ValueError(f"unknown mode {mode!r}")


class SignalToData(NodeDefinition):
    @staticmethod
    @group(trigger="pass")
    def pass_value(
        gate: Annotated[bool, SignalMarker()],
        x: Annotated[int, GatedMarker("gate")],
    ) -> DataEvent:
        return DataEvent(value=x)


PRIMITIVE_DEFINITIONS = (Source, Constant, Sink, Probe, Buffer, Join, Split, Latch, DataToSignal, SignalToData)
PRIMITIVES = {definition.TYPE.name: definition.TYPE for definition in PRIMITIVE_DEFINITIONS}
