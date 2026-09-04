"""Composable readiness predicate DSL（REFACTOR_READINESS_VALIDATION 扩展）。

谓词协议 = 可组合求值 + 可观察性 + 编译期查询：
- evaluate(data, trigger)：求值
- explain(data, trigger)：人读解释（调试可视化，哪个子条件失败）
- requires_port_pending(port)：该谓词是否要求某端口 pending——
  允许 over-report（假阳性），不允许 under-report（假阴性），供编译期分析
- referenced_ports()：引用的全部端口（构建期校验存在性、编辑器列依赖）

explain() 会重评估叶子条件（回调为纯读，调试路径可接受）。
√ / × 均为 GBK 可编码字符（Windows 控制台安全）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class Readiness(Protocol):
    def evaluate(self, data, trigger) -> bool: ...

    def explain(self, data, trigger) -> str: ...

    def requires_port_pending(self, port: str) -> bool: ...

    def referenced_ports(self) -> set[str]: ...


@dataclass(frozen=True)
class _Data:
    port: str

    def evaluate(self, data, trigger):
        return data(self.port)

    def explain(self, data, trigger):
        return f"DATA({self.port!r}) = {data(self.port)}"

    def requires_port_pending(self, port: str) -> bool:
        return self.port == port

    def referenced_ports(self) -> set[str]:
        return {self.port}


@dataclass(frozen=True)
class _Trigger:
    port: str

    def evaluate(self, data, trigger):
        return trigger(self.port)

    def explain(self, data, trigger):
        return f"TRIGGER({self.port!r}) = {trigger(self.port)}"

    def requires_port_pending(self, port: str) -> bool:
        return self.port == port

    def referenced_ports(self) -> set[str]:
        return {self.port}


@dataclass(frozen=True)
class _All:
    conds: tuple[Readiness, ...]

    def evaluate(self, data, trigger):
        return all(c.evaluate(data, trigger) for c in self.conds)

    def explain(self, data, trigger):
        parts, ok = [], True
        for c in self.conds:
            result = c.evaluate(data, trigger)
            ok = ok and result
            parts.append(f"  {'√' if result else '×'} {c.explain(data, trigger)}")
        status = "AND: all conditions met" if ok else "AND failed"
        return f"{status}:\n" + "\n".join(parts)

    def requires_port_pending(self, port: str) -> bool:
        """AND：任一子式要求该端口即要求（全部满足才 ready）。"""
        return any(c.requires_port_pending(port) for c in self.conds)

    def referenced_ports(self) -> set[str]:
        return set().union(*(c.referenced_ports() for c in self.conds))


@dataclass(frozen=True)
class _Any:
    conds: tuple[Readiness, ...]

    def evaluate(self, data, trigger):
        return any(c.evaluate(data, trigger) for c in self.conds)

    def explain(self, data, trigger):
        parts, ok = [], False
        for c in self.conds:
            result = c.evaluate(data, trigger)
            ok = ok or result
            parts.append(f"  {'√' if result else '×'} {c.explain(data, trigger)}")
        status = "OR: at least one condition met" if ok else "OR: all failed"
        return f"{status}:\n" + "\n".join(parts)

    def requires_port_pending(self, port: str) -> bool:
        """ANY（保守）：仅当所有分支都要求该端口时才要求——
        否则存在不依赖该端口的可满足分支。"""
        if not self.conds:
            return False
        return all(c.requires_port_pending(port) for c in self.conds)

    def referenced_ports(self) -> set[str]:
        return set().union(*(c.referenced_ports() for c in self.conds))


def DATA(port: str) -> Readiness:
    return _Data(port)


def TRIGGER(port: str) -> Readiness:
    return _Trigger(port)


def ALL(*conds: Readiness) -> Readiness:
    return _All(tuple(conds))


def ANY(*conds: Readiness) -> Readiness:
    return _Any(tuple(conds))
