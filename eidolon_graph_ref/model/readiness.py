"""Composable readiness predicate DSL."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol
class Readiness(Protocol):
    def evaluate(self, data, trigger) -> bool: ...
@dataclass(frozen=True)
class _Data:
    port: str
    def evaluate(self, data, trigger): return data(self.port)
@dataclass(frozen=True)
class _Trigger:
    port: str
    def evaluate(self, data, trigger): return trigger(self.port)
@dataclass(frozen=True)
class _All:
    conds: tuple[Readiness, ...]
    def evaluate(self, data, trigger): return all(c.evaluate(data, trigger) for c in self.conds)
@dataclass(frozen=True)
class _Any:
    conds: tuple[Readiness, ...]
    def evaluate(self, data, trigger): return any(c.evaluate(data, trigger) for c in self.conds)
def DATA(port: str) -> Readiness: return _Data(port)
def TRIGGER(port: str) -> Readiness: return _Trigger(port)
def ALL(*conds: Readiness) -> Readiness: return _All(tuple(conds))
def ANY(*conds: Readiness) -> Readiness: return _Any(tuple(conds))
