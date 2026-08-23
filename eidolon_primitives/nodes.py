"""Built-in nodes expressed in the Node Definition Language (DSL v2).

Same package standing as any external node package: this module only produces
NodeType values via the DSL front-end; the kernel never imports it.
See docs/graph-node-definition-dsl.md for the language semantics.
"""
from eidolon_dsl import Append, Config, Gated, NodeDefinition, Signal, State, Trigger, group


class Source(NodeDefinition):
    count: State[int] = 0

    @group(defaults={"step": 1})
    def tick(this, trigger: Trigger, cfg: Config) -> int:
        count = this.count
        this.count += cfg["step"]
        return count


class Constant(NodeDefinition):
    @group(defaults={"value": 0})
    def tick(trigger: Trigger, cfg: Config) -> int:
        return cfg["value"]


class Sink(NodeDefinition):
    last: State[int | None] = None

    @group
    def consume(this, value: int) -> None:
        this.last = value


class Probe(NodeDefinition):
    log: State[list[int]] = []

    @group
    def observe(this, value: int) -> None:
        this.log.append(value)


class Buffer(NodeDefinition):
    items: State[list] = []

    @group
    def put(this, item: Append[int]) -> None:
        this.items.extend(item)

    @group
    def flush(this, trigger: Trigger) -> list:
        items = this.items
        this.items = []
        return items if items else None


class Join(NodeDefinition):
    @group
    def join(a: int, b: int) -> tuple[int, int]:
        return (a, b)


class Split(NodeDefinition):
    @group(outputs=("out1", "out2"))
    def fan(value: int) -> tuple[int, int]:
        return (value, value)


class Latch(NodeDefinition):
    @group
    def release(gate: Signal, trigger: Trigger, data: Gated[int, "gate"]) -> int:
        return data


class DataToSignal(NodeDefinition):
    @group(defaults={"mode": "truthy", "threshold": 0})
    def convert(cfg: Config, data: int) -> Signal[bool]:
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
    def pass_value(gate: Signal, x: Gated[int, "gate"]) -> int:
        return x


PRIMITIVE_DEFINITIONS = (Source, Constant, Sink, Probe, Buffer, Join, Split, Latch, DataToSignal, SignalToData)
PRIMITIVES = {definition.TYPE.name: definition.TYPE for definition in PRIMITIVE_DEFINITIONS}
