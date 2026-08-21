"""内置节点包:10 个验证原语(节点协议 ABI 的 builtin 实现者)。

与外部节点包地位完全相同:本包只提供 NodeType 值,经宿主 types 字典注册
(graph-node-protocol.md §8)。内核包(eidolon_graph_ref)不认识本包——
内核只消费符合协议的 NodeType,不拥有任何节点(test_bootstrap.py 锁定)。
"""

from .nodes import PRIMITIVES

__all__ = ["PRIMITIVES"]
