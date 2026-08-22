"""A third-party Group-centric node; the kernel owns no node registry."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eidolon_graph_ref.engine import GraphInstance, GroupOutput, Injection, Kind
from eidolon_graph_ref.model import DATA, DataIn, DataOut, GraphDefinition, GroupSpec, NodeDefinition, SLOT_DATA
from eidolon_primitives import PRIMITIVES

def count_words(ctx):
    return GroupOutput(data_out={"count": len(ctx.data_in["text"].split())}, state={"runs": ctx.state["runs"] + 1})

class WordCount(NodeDefinition):
    data_in = (DataIn("text"),)
    data_out = (DataOut("count"),)
    state_defaults = {"runs": 0}
    groups = (GroupSpec("count", inputs=("text",), outputs=("count",), readiness=DATA("text"), handler="count_words"),)

    count_words = staticmethod(count_words)

if __name__ == "__main__":
    graph = GraphDefinition("external")
    graph.add_node("wc", "WordCount")
    graph.add_node("sink", "Sink")
    graph.wire("wc", "count", "sink", "in")
    world = GraphInstance.build(graph, {**PRIMITIVES, "WordCount": WordCount.TYPE}).instance
    world.run([Injection("wc", "text", SLOT_DATA, Kind.DATA, "group handler ABI")])
    print(world.observable_state())
