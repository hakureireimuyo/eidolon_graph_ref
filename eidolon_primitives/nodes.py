"""Built-in nodes expressed in the Node Definition Language."""
from eidolon_graph_ref.engine.protocol import GroupOutput
from eidolon_graph_ref.model import (
    ANY,
    DATA,
    APPEND,
    DataIn,
    DataOut,
    GroupSpec,
    NodeDefinition,
    SignalIn,
    SignalOut,
    TriggerIn,
)


class Source(NodeDefinition):
    data_out = (DataOut("out"),)
    trigger_in = (TriggerIn("tick"),)
    state_defaults = {"count": 0}
    tags = ("source",)
    groups = (GroupSpec("tick", triggers=("tick",), outputs=("out",), defaults={"step": 1}, handler="tick"),)

    @staticmethod
    def tick(ctx):
        return GroupOutput(data_out={"out": ctx.state["count"]}, state={"count": ctx.state["count"] + ctx.config["step"]})


class Constant(NodeDefinition):
    data_out = (DataOut("out"),)
    trigger_in = (TriggerIn("tick"),)
    tags = ("source",)
    groups = (GroupSpec("tick", triggers=("tick",), outputs=("out",), defaults={"value": 0}, handler="tick"),)

    @staticmethod
    def tick(ctx):
        return GroupOutput(data_out={"out": ctx.config["value"]})


class Sink(NodeDefinition):
    data_in = (DataIn("in"),)
    state_defaults = {"last": None}
    groups = (GroupSpec("in", inputs=("in",), handler="consume", readiness=ANY(DATA("in"))),)

    @staticmethod
    def consume(ctx):
        return GroupOutput(state={"last": ctx.data_in["in"]})


class Probe(NodeDefinition):
    data_in = (DataIn("in"),)
    state_defaults = {"log": []}
    groups = (GroupSpec("in", inputs=("in",), handler="observe", readiness=ANY(DATA("in"))),)

    @staticmethod
    def observe(ctx):
        return GroupOutput(state={"log": [*ctx.state["log"], ctx.data_in["in"]]})


class Buffer(NodeDefinition):
    data_in = (DataIn("put", cache=APPEND),)
    data_out = (DataOut("out"),)
    trigger_in = (TriggerIn("flush"),)
    state_defaults = {"items": []}
    groups = (
        GroupSpec("put", inputs=("put",), handler="put", readiness=ANY(DATA("put"))),
        GroupSpec("flush", triggers=("flush",), outputs=("out",), handler="flush"),
    )

    @staticmethod
    def put(ctx):
        return GroupOutput(state={"items": list(ctx.data_in["put"])})

    @staticmethod
    def flush(ctx):
        items = list(ctx.state["items"])
        return GroupOutput(data_out={"out": items} if items else {}, state={"items": []})


class Join(NodeDefinition):
    data_in = (DataIn("a"), DataIn("b"))
    data_out = (DataOut("out"),)
    groups = (GroupSpec("sync", inputs=("a", "b"), outputs=("out",), handler="join"),)

    @staticmethod
    def join(ctx):
        return GroupOutput(data_out={"out": (ctx.data_in["a"], ctx.data_in["b"])})


class Split(NodeDefinition):
    data_in = (DataIn("in"),)
    data_out = (DataOut("out1"), DataOut("out2"))
    groups = (GroupSpec("fan", inputs=("in",), outputs=("out1", "out2"), handler="split", readiness=ANY(DATA("in"))),)

    @staticmethod
    def split(ctx):
        return GroupOutput(data_out={"out1": ctx.data_in["in"], "out2": ctx.data_in["in"]})


class Latch(NodeDefinition):
    data_in = (DataIn("data", signal="gate"),)
    data_out = (DataOut("out"),)
    trigger_in = (TriggerIn("release"),)
    signal_in = (SignalIn("gate"),)
    groups = (GroupSpec("release", inputs=("data",), triggers=("release",), outputs=("out",), handler="release"),)

    @staticmethod
    def release(ctx):
        return GroupOutput(data_out={"out": ctx.data_in["data"]})


class DataToSignal(NodeDefinition):
    data_in = (DataIn("data"),)
    signal_out = (SignalOut("level"),)
    groups = (GroupSpec("convert", inputs=("data",), outputs=("level",), defaults={"mode": "truthy", "threshold": 0}, handler="convert", readiness=ANY(DATA("data"))),)

    @staticmethod
    def convert(ctx):
        value, mode, threshold = ctx.data_in["data"], ctx.config["mode"], ctx.config["threshold"]
        levels = {"truthy": bool(value), "gt": value > threshold, "lt": value < threshold, "eq": value == threshold}
        if mode not in levels:
            raise ValueError(f"unknown mode {mode!r}")
        return GroupOutput(signal_out={"level": levels[mode]})


class SignalToData(NodeDefinition):
    data_in = (DataIn("x", signal="gate"),)
    data_out = (DataOut("out"),)
    trigger_in = (TriggerIn("pass"),)
    signal_in = (SignalIn("gate"),)
    groups = (GroupSpec("pass", inputs=("x",), triggers=("pass",), outputs=("out",), handler="pass_value"),)

    @staticmethod
    def pass_value(ctx):
        return GroupOutput(data_out={"out": ctx.data_in["x"]})


PRIMITIVE_DEFINITIONS = (Source, Constant, Sink, Probe, Buffer, Join, Split, Latch, DataToSignal, SignalToData)
PRIMITIVES = {definition.TYPE.name: definition.TYPE for definition in PRIMITIVE_DEFINITIONS}
