"""The Python-side Node Definition Language.

``NodeDefinition`` classes are compile-time declarations only.  The graph
kernel receives the compiled ``NodeType`` value and never constructs a node
object or consults this module while executing a graph.  ``NodeType`` is the
compile target of this front-end: the semantic IR (Node ABI) the kernel
executes (docs/graph-node-protocol.md §1.0).

Capability ownership (frozen, docs/graph-node-protocol.md §2.0): a concrete
node definition is a declaration entry point, never a behavior supplier for
another concrete node definition.  Universal node semantics live in the
kernel; node-specific shared behavior may be reused through plain (non-
definition) material classes, i.e. mixins.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import inspect
from typing import Any, Callable

from .node_type import Group, NodeType
from .readiness import Readiness


class DefinitionError(TypeError):
    """A node source declaration cannot be compiled into the kernel ABI."""


@dataclass(frozen=True)
class GroupSpec:
    """Definition-language group declaration.

    ``handler`` is a method name, deliberately not a callable.  Resolution is
    performed once at class creation, before the compiled ``Group`` reaches
    the kernel.
    """

    name: str
    inputs: tuple[str, ...] = ()
    triggers: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    defaults: dict[str, Any] = field(default_factory=dict)
    handler: str = ""
    readiness: Readiness | None = None


def _static_handler(owner: type, name: str) -> Callable[..., Any]:
    """Resolve and verify the source-level handler ABI without binding ``self``."""

    descriptor = inspect.getattr_static(owner, name)
    if not isinstance(descriptor, staticmethod):
        raise DefinitionError(f"{owner.__name__}.{name} must be declared with @staticmethod")
    func = descriptor.__func__
    signature = inspect.signature(func)
    parameters = tuple(signature.parameters.values())
    if len(parameters) != 1 or any(
        p.kind not in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        or p.default is not inspect.Parameter.empty
        for p in parameters
    ):
        raise DefinitionError(f"{owner.__name__}.{name} must have exactly one required ctx parameter")
    return func


class NodeDefinitionMeta(type):
    """Compile a definition class into a protocol-frozen ``NodeType``."""

    def __new__(mcls, name: str, bases: tuple[type, ...], namespace: dict[str, Any], **kwargs: Any):
        if name != "NodeDefinition":
            for base in bases:
                if isinstance(base, NodeDefinitionMeta) and base.__name__ != "NodeDefinition":
                    raise DefinitionError(
                        f"{name} cannot inherit from concrete node definition "
                        f"{base.__name__}; share materials instead"
                    )
        cls: type = super().__new__(mcls, name, bases, namespace, **kwargs)
        if name == "NodeDefinition":
            return cls
        cls.TYPE = mcls._compile(cls, namespace)  # type: ignore[attr-defined]
        return cls

    @staticmethod
    def _compile(target: type, namespace: dict[str, Any]) -> NodeType:
        specs = tuple(getattr(target, "groups", ()))
        if not all(isinstance(spec, GroupSpec) for spec in specs):
            raise DefinitionError(f"{target.__name__}.groups must contain GroupSpec values")
        compiled: list[Group] = []
        for spec in specs:
            if not spec.handler:
                raise DefinitionError(f"{target.__name__} group {spec.name!r} has no handler name")
            compiled.append(
                Group(
                    name=spec.name,
                    inputs=tuple(spec.inputs),
                    triggers=tuple(spec.triggers),
                    outputs=tuple(spec.outputs),
                    defaults=dict(spec.defaults),
                    handler=_static_handler(target, spec.handler),
                    readiness=spec.readiness,
                )
            )
        try:
            node_type = NodeType(
                name=namespace.get("type_name", target.__name__),
                data_in=tuple(getattr(target, "data_in", ())),
                data_out=tuple(getattr(target, "data_out", ())),
                trigger_in=tuple(getattr(target, "trigger_in", ())),
                signal_in=tuple(getattr(target, "signal_in", ())),
                signal_out=tuple(getattr(target, "signal_out", ())),
                asset_in=tuple(getattr(target, "asset_in", ())),
                state_defaults=dict(getattr(target, "state_defaults", {})),
                init_defaults=dict(getattr(target, "init_defaults", {})),
                groups=tuple(compiled),
                tags=tuple(getattr(target, "tags", ())),
                init=getattr(target, "init", None),
                doc=getattr(target, "doc", None),
            )
        except ValueError as e:
            raise DefinitionError(f"{target.__name__}: {e}") from e
        return node_type


class NodeDefinition(metaclass=NodeDefinitionMeta):
    """Base class for compile-time node definitions; never instantiate it."""

    TYPE: NodeType

    def __new__(cls, *args: Any, **kwargs: Any):
        raise TypeError("NodeDefinition classes are compile-time declarations; use .TYPE in a graph")
