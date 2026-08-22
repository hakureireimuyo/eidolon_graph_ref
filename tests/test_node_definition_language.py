"""Definition-language tests: concrete nodes are flat; reuse flows through materials.

Frozen boundary (docs/graph-node-protocol.md §2.0): a concrete node definition
is a declaration entry point, never a behavior supplier for another concrete
node definition.  Shared behavior is carried by plain material classes (mixins)
that do not compile a TYPE and never reach the kernel.
"""
import pytest

from eidolon_graph_ref.engine import GraphInstance, Injection, Kind, NodeSemantics
from eidolon_graph_ref.engine.protocol import GroupOutput
from eidolon_graph_ref.model import (
    DATA,
    DataIn,
    DataOut,
    DefinitionError,
    GraphDefinition,
    Group,
    GroupSpec,
    NodeDefinition,
    NodeType,
    SignalIn,
    SLOT_DATA,
    SLOT_SIGNAL,
    SLOT_TRIGGER,
    TriggerIn,
)


class Echo(NodeDefinition):
    data_in = (DataIn("value"),)
    data_out = (DataOut("out"),)
    groups = (GroupSpec("run", inputs=("value",), outputs=("out",), handler="run", readiness=DATA("value")),)

    @staticmethod
    def run(ctx):
        return GroupOutput(data_out={"out": ctx.data_in["value"]})


def test_concrete_node_cannot_be_a_behavior_supplier():
    with pytest.raises(DefinitionError, match="concrete node definition"):
        class Illegal(Echo):
            pass


def test_definition_classes_cannot_be_instantiated():
    with pytest.raises(TypeError, match="compile-time declarations"):
        Echo()


def test_handlers_must_be_static_and_have_exactly_one_argument():
    with pytest.raises(DefinitionError, match="@staticmethod"):
        class Illegal(NodeDefinition):
            groups = (GroupSpec("run", handler="run", readiness=DATA("x")),)

            def run(self, ctx):
                return GroupOutput()

    with pytest.raises(DefinitionError, match="exactly one required ctx"):
        class AlsoIllegal(NodeDefinition):
            groups = (GroupSpec("run", handler="run", readiness=DATA("x")),)

            @staticmethod
            def run(ctx, extra):
                return GroupOutput()


def test_material_mixin_compiles_and_runs():
    class EchoMaterial:
        data_in = (DataIn("v"),)
        data_out = (DataOut("out"),)
        groups = (GroupSpec("echo", inputs=("v",), outputs=("out",), handler="echo", readiness=DATA("v")),)

        @staticmethod
        def echo(ctx):
            return GroupOutput(data_out={"out": ctx.data_in["v"]})

    class MaterialNode(NodeDefinition, EchoMaterial):
        pass

    assert MaterialNode.TYPE.name == "MaterialNode"
    graph = GraphDefinition()
    graph.add_node("node", "MaterialNode")
    report = GraphInstance.build(graph, {"MaterialNode": MaterialNode.TYPE})
    assert report.ok
    world = report.instance
    world.run([Injection("node", "v", SLOT_DATA, Kind.DATA, "hello")])
    assert [e.payload for e in world.timeline.events.values() if e.producer == "node"] == ["hello"]


def test_concrete_class_shadows_material_handler():
    class EchoMaterial:
        data_in = (DataIn("v"),)
        data_out = (DataOut("out"),)
        groups = (GroupSpec("echo", inputs=("v",), outputs=("out",), handler="echo", readiness=DATA("v")),)

        @staticmethod
        def echo(ctx):
            return GroupOutput(data_out={"out": ctx.data_in["v"]})

    class Doubling(NodeDefinition, EchoMaterial):
        @staticmethod
        def echo(ctx):
            return GroupOutput(data_out={"out": ctx.data_in["v"] * 2})

    assert Doubling.TYPE.groups[0].handler is Doubling.__dict__["echo"].__func__
    graph = GraphDefinition()
    graph.add_node("node", "Doubling")
    report = GraphInstance.build(graph, {"Doubling": Doubling.TYPE})
    assert report.ok
    world = report.instance
    world.run([Injection("node", "v", SLOT_DATA, Kind.DATA, 21)])
    assert [e.payload for e in world.timeline.events.values() if e.producer == "node"] == [42]


def test_final_node_semantics_interprets_data_signal_and_trigger_orthogonally():
    controlled = NodeType(
        "Controlled",
        data_in=(DataIn("value", default="fallback", signal="gate"),),
        data_out=(DataOut("out"),),
        trigger_in=(TriggerIn("go"),),
        signal_in=(SignalIn("gate"),),
        groups=(
            Group(
                "go",
                inputs=("value",),
                triggers=("go",),
                outputs=("out",),
                handler=lambda ctx: GroupOutput(data_out={"out": ctx.data_in["value"]}),
            ),
        ),
    )
    graph = GraphDefinition()
    graph.add_node("node", "Controlled")
    report = GraphInstance.build(graph, {"Controlled": controlled})
    assert report.ok
    world = report.instance

    world.run([
        Injection("node", "gate", SLOT_SIGNAL, Kind.SIGNAL, False),
        Injection("node", "go", SLOT_TRIGGER, Kind.SIGNAL, True),
    ])
    assert [event.payload for event in world.timeline.events.values() if event.producer == "node"] == ["fallback"]

    world.run([
        Injection("node", "gate", SLOT_SIGNAL, Kind.SIGNAL, True),
        Injection("node", "value", SLOT_DATA, Kind.DATA, "dynamic"),
        Injection("node", "go", SLOT_TRIGGER, Kind.SIGNAL, True),
    ])
    assert [event.payload for event in world.timeline.events.values() if event.producer == "node"] == ["fallback", "dynamic"]

    with pytest.raises(TypeError, match="final"):
        class Illegal(NodeSemantics):
            pass
