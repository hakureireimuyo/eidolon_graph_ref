"""Registry boundary between compiled node types and graph editing."""
from __future__ import annotations

from collections.abc import Iterator, Mapping

from .node_type import NodeType


class NodeRegistry(Mapping[str, NodeType]):
    """The catalog visible to graph validation.

    Definitions are compiled before they enter this registry. Graphs refer
    only to the resulting ``NodeType`` values; the registry never imports or
    interprets ``NodeDefinition`` classes.
    """

    def __init__(self, types: Mapping[str, NodeType] | None = None):
        self._types: dict[str, NodeType] = {}
        for node_type in (types or {}).values():
            self.register(node_type)

    def register(self, node_type: NodeType) -> NodeType:
        if not isinstance(node_type, NodeType):
            raise TypeError("NodeRegistry accepts compiled NodeType values")
        if node_type.name in self._types:
            raise ValueError(f"duplicate node type {node_type.name!r}")
        self._types[node_type.name] = node_type
        return node_type

    def __getitem__(self, name: str) -> NodeType:
        return self._types[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._types)

    def __len__(self) -> int:
        return len(self._types)