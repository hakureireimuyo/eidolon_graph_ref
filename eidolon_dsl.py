"""Eidolon Node Definition DSL v2 prototype — function signatures as group contracts.

Design baseline (ratified in-session 2026-08-22; formal semantic doc pending)::

    class Counter(NodeDefinition):
        count: State[int] = 0

        @group
        def tick(this, trigger: Trigger, step: int = 1) -> int:
            this.count += step
            return this.count

The DSL borrows Python *syntax*, not Python semantics:

- one ``@group`` function == one Group; function name == group name
- parameters == group inputs; ``trigger: Trigger`` / ``gate: Signal`` are
  special declarations, not data inputs
- parameter default == DataIn fallback for an absent input event (NOT a
  graph-config default; ``@group(defaults=...)`` is the config surface)
- return annotation == output declaration; ``-> None`` == no output event;
  ``-> Signal[bool]`` == signal output; ``@group(outputs=(...))`` plus a
  tuple return is the multi-output extension
- ``this`` (optional first parameter) == restricted state view: reads and
  whole-value writes only; in-place mutation of a ``this.x`` value is a
  no-op; undeclared fields raise AttributeError (recorded as KIND_ERROR)
- ``Gated[T, "gate"]`` == data input whose validity a signal level helps
  interpret.  The ``by=`` keyword form is impossible in Python (PEP 637
  subscript kwargs never landed — it is a SyntaxError), so the second
  positional argument is the binding target
- port names are group-qualified: ``"{group}.{param}"`` — ports belong to
  groups, so two groups may declare parameters with the same name

The compiler reuses the existing ``GroupSpec → NodeType`` pipeline: the
compiled ``NodeType`` is exactly the IR the kernel consumes, and the kernel
has zero knowledge of this front-end.
"""
from __future__ import annotations

import inspect
import sys
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, get_type_hints

from eidolon_graph_ref.engine.protocol import GroupOutput
from eidolon_graph_ref.model.assets import AssetIn
from eidolon_graph_ref.model.definition import DefinitionError, GroupSpec, NodeDefinitionMeta
from eidolon_graph_ref.model.node_type import NodeType
from eidolon_graph_ref.model.ports import APPEND, REPLACE, DataIn, DataOut, SignalIn, SignalOut, TriggerIn
from eidolon_graph_ref.model.readiness import _All, _Any, _Data, _Trigger


# ---- annotation vocabulary --------------------------------------------------


class _Marker:
    """Result of subscripting a DSL annotation (``Gated[int, "gate"]`` etc.)."""

    def __init__(self, args: tuple):
        self.args = args

    def __repr__(self) -> str:
        return f"<{self.args[0]}>"


class State:
    """Node-level state declaration (class annotation only)."""

    def __class_getitem__(cls, t) -> _Marker:
        return _Marker(("state", t))


class Trigger:
    """Group activation condition (parameter annotation)."""


class Config:
    """Merged group config (parameter annotation): the parameter receives
    ``{**group.defaults, **graph group config}``.  ``this`` remains state-only;
    config enters the body only through this declared parameter."""





class Signal:
    """Signal dependency (parameter annotation) / signal output (return annotation)."""

    def __class_getitem__(cls, t) -> _Marker:
        return _Marker(("signal_out", t))


class Gated:
    """Data input whose validity a signal level participates in interpreting."""

    def __class_getitem__(cls, args) -> _Marker:
        # ``Gated[int, "gate"]`` reaches __class_getitem__ as ONE tuple argument
        t, binding = args
        return _Marker(("gated", t, binding))


class Append:
    """Cumulative data input (DataIn cache=APPEND)."""

    def __class_getitem__(cls, t) -> _Marker:
        return _Marker(("append", t))


class Asset:
    """Asset dependency declared at a group parameter, compiled to node level."""

    def __class_getitem__(cls, t) -> _Marker:
        return _Marker(("asset", t))


def _is_state(ann: Any) -> bool:
    return ann is State or (isinstance(ann, _Marker) and ann.args[0] == "state")


# ---- @group decorator --------------------------------------------------------


@dataclass(frozen=True)
class _GroupOpts:
    readiness: Any = None
    defaults: dict = field(default_factory=dict)
    outputs: tuple = ()
    trigger: str | None = None


def group(fn=None, *, readiness=None, defaults=None, outputs=(), trigger=None):
    """Declare the decorated function as an Eidolon Group.

    Decoration only tags the function for the ``NodeDefinition`` metaclass;
    the function is never invoked as a Python method at runtime.
    """

    opts = _GroupOpts(readiness=readiness, defaults=dict(defaults or {}), outputs=tuple(outputs or ()), trigger=trigger)

    def deco(fn):
        setattr(fn, "_eidolon_group", opts)
        return fn

    return deco if fn is None else deco(fn)


# ---- state proxy -------------------------------------------------------------


class _StateProxy:
    """``this`` — a restricted view of node state inside a group function.

    Reads return the snapshot value; writes record whole-value deltas.
    In-place mutation of a read value mutates the proxy's private copy and
    records nothing, so it is a no-op by construction.
    """

    def __init__(self, snapshot: dict):
        object.__setattr__(self, "_snapshot", snapshot)
        object.__setattr__(self, "_delta", {})

    def __getattr__(self, name: str):
        try:
            return object.__getattribute__(self, "_snapshot")[name]
        except KeyError:
            raise AttributeError(f"undeclared state field {name!r}") from None

    def __setattr__(self, name: str, value: Any):
        snapshot = object.__getattribute__(self, "_snapshot")
        if name not in snapshot:
            raise AttributeError(
                f"undeclared state field {name!r} (declared: {sorted(snapshot)})"
            )
        snapshot[name] = value
        object.__getattribute__(self, "_delta")[name] = value

    @property
    def delta(self) -> dict:
        return object.__getattribute__(self, "_delta")


# ---- compilation -------------------------------------------------------------


@dataclass
class _Param:
    name: str
    role: str  # this | trigger | signal | data | append | gated | asset
    port: str  # group-qualified port name; "" for this
    default: Any = None
    has_default: bool = False
    binding: str | None = None  # Gated binding target (parameter name)
    asset_type: Any = None


def _annotations(fn) -> dict:
    try:
        return dict(get_type_hints(fn))
    except Exception:
        return dict(getattr(fn, "__annotations__", {}))


def _compile_group(cls_name: str, fn, opts: _GroupOpts):
    gname = fn.__name__
    hints = _annotations(fn)
    psig = list(inspect.signature(fn).parameters.values())
    if not psig:
        raise DefinitionError(f"{cls_name}.{gname}: group function must declare at least one parameter")

    params: list[_Param] = []
    for i, p in enumerate(psig):
        ann = hints.get(p.name)
        has_default = p.default is not inspect.Parameter.empty
        if p.name == "this":
            if i != 0:
                raise DefinitionError(f"{cls_name}.{gname}: 'this' must be the first parameter")
            if ann is not None:
                raise DefinitionError(f"{cls_name}.{gname}: 'this' takes no annotation")
            params.append(_Param("this", "this", ""))
            continue
        if p.name == "self":
            raise DefinitionError(
                f"{cls_name}.{gname}: groups do not accept a Python instance receiver; "
                "use 'this' for runtime node state"
            )
        if p.kind not in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            raise DefinitionError(f"{cls_name}.{gname}: parameter {p.name!r} must be positional")
        port = f"{gname}.{p.name}"
        if ann is Trigger:
            params.append(_Param(p.name, "trigger", port))
        elif ann is Config:
            params.append(_Param(p.name, "config", ""))
        elif ann is Signal:
            params.append(_Param(p.name, "signal", port))
        elif isinstance(ann, _Marker):
            kind = ann.args[0]
            if kind == "gated":
                binding = ann.args[2]  # ("gated", type, binding)
                if not isinstance(binding, str):
                    raise DefinitionError(
                        f"{cls_name}.{gname}: Gated binding must be a string, got {binding!r}"
                    )
                params.append(
                    _Param(p.name, "gated", port, p.default if has_default else None, has_default, binding)
                )
            elif kind == "append":
                params.append(_Param(p.name, "append", port, p.default if has_default else None, has_default))
            elif kind == "asset":
                if has_default:
                    raise DefinitionError(f"{cls_name}.{gname}: asset parameter {p.name!r} takes no default")
                params.append(_Param(p.name, "asset", port, asset_type=ann.args[1]))
            else:
                raise DefinitionError(f"{cls_name}.{gname}: unknown annotation {ann!r}")
        else:
            params.append(_Param(p.name, "data", port, p.default if has_default else None, has_default))

    # ordering rule: this → specials → required data → defaulted data
    phase = "special"
    for p in params:
        if p.role == "this":
            continue
        if p.role in ("trigger", "signal", "asset", "config"):
            if phase != "special":
                raise DefinitionError(f"{cls_name}.{gname}: special parameter {p.name!r} must precede data inputs")
            if p.role in ("trigger", "signal", "config") and p.has_default:
                raise DefinitionError(f"{cls_name}.{gname}: {p.role} parameter {p.name!r} takes no default")
        else:
            if phase == "special":
                phase = "required"
            if p.has_default:
                phase = "defaulted"
            elif phase == "defaulted":
                raise DefinitionError(f"{cls_name}.{gname}: required data input {p.name!r} must precede defaulted inputs")

    # outputs
    ret = hints.get("return", inspect.Signature.empty)
    if ret is inspect.Signature.empty or ret is None or ret is type(None):
        out_kind = None
    elif isinstance(ret, _Marker) and ret.args[0] == "signal_out":
        out_kind = "signal"
    else:
        out_kind = "data"
    if opts.outputs:
        if out_kind != "data":
            raise DefinitionError(f"{cls_name}.{gname}: outputs= requires a data return annotation")
        out_names = tuple(f"{gname}.{n}" for n in opts.outputs)  # 裁定 2:所有端口组限定
    else:
        out_names = (gname,) if out_kind else ()

    # port tables
    data_ins, signal_ins, trigger_ins, asset_ins = [], [], [], []
    signal_ports = {p.port for p in params if p.role == "signal"}
    bound_signals: dict[str, str] = {}
    for p in params:
        if p.role == "trigger":
            trigger_ins.append(TriggerIn(p.port))
        elif p.role == "signal":
            signal_ins.append(SignalIn(p.port))
        elif p.role == "asset":
            asset_ins.append(AssetIn(p.name, p.asset_type))
        elif p.role in ("data", "append", "gated"):
            if p.role == "gated":
                target = f"{gname}.{p.binding}"
                if p.binding not in {q.name for q in params if q.role == "signal"}:
                    raise DefinitionError(
                        f"{cls_name}.{gname}: Gated binding {p.binding!r} must reference a Signal parameter"
                    )
                if target in bound_signals:
                    raise DefinitionError(
                        f"{cls_name}.{gname}: signal {p.binding!r} already gates {bound_signals[target]!r}"
                    )
                bound_signals[target] = p.name
            data_ins.append(
                DataIn(
                    p.port,
                    default=p.default,
                    cache=APPEND if p.role == "append" else REPLACE,
                    signal=(f"{gname}.{p.binding}" if p.role == "gated" else None),
                )
            )
    inputs = [
        p.port
        for p in params
        if p.role in ("data", "append", "gated") or (p.role == "signal" and p.port not in bound_signals)
    ]
    if opts.trigger:
        if any(p.role == "trigger" for p in params):
            raise DefinitionError(f"{cls_name}.{gname}: trigger= and a Trigger parameter are mutually exclusive")
        trigger_ins.append(TriggerIn(f"{gname}.{opts.trigger}"))
        triggers = (f"{gname}.{opts.trigger}",)
    else:
        triggers = tuple(p.port for p in params if p.role == "trigger")

    spec = GroupSpec(
        name=gname,
        inputs=tuple(inputs),
        triggers=triggers,
        outputs=out_names,
        defaults=dict(opts.defaults),
        handler=gname,
        readiness=_qualify_readiness(opts.readiness, gname),
    )
    wrapper = _make_wrapper(fn, params, out_kind, out_names)
    ports = {
        "data": data_ins,
        "signal": signal_ins,
        "trigger": trigger_ins,
        "asset": asset_ins,
        "out_data": [DataOut(n) for n in out_names] if out_kind == "data" else [],
        "out_signal": [SignalOut(n) for n in out_names] if out_kind == "signal" else [],
    }
    return spec, wrapper, ports


def _qualify_readiness(pred, gname: str):
    """Group-qualify leaf ports of a readiness predicate (``DATA("a")`` → ``DATA("add.a")``)."""

    if pred is None:
        return None
    if isinstance(pred, (_Data, _Trigger)):
        if "." in pred.port:
            return pred
        return type(pred)(f"{gname}.{pred.port}")
    if isinstance(pred, (_All, _Any)):
        return type(pred)(tuple(_qualify_readiness(c, gname) for c in pred.conds))
    raise DefinitionError(f"unsupported readiness predicate {pred!r}")


def _make_wrapper(fn, params: list[_Param], out_kind, out_names: tuple):
    def handler(ctx):
        proxy = _StateProxy(deepcopy(ctx.state)) if any(p.role == "this" for p in params) else None
        args = []
        for p in params:
            if p.role == "this":
                args.append(proxy)
            elif p.role in ("data", "append", "gated", "trigger", "signal"):
                args.append(ctx.data_in.get(p.port))
            elif p.role == "config":
                args.append(ctx.config)
            else:  # asset — build-time resolved capability, passed as the argument value
                args.append(ctx.assets.get(p.name))
        result = fn(*args)
        out = GroupOutput()
        if result is not None and out_names:
            if out_kind == "signal":
                out.signal_out[out_names[0]] = result
            elif len(out_names) == 1:
                out.data_out[out_names[0]] = result
            else:
                if not isinstance(result, tuple) or len(result) != len(out_names):
                    raise TypeError(
                        f"group with outputs={out_names} must return a {len(out_names)}-tuple, got {result!r}"
                    )
                out.data_out = dict(zip(out_names, result))
        if proxy is not None and proxy.delta:
            out.state = proxy.delta
        return out

    return handler


# ---- NodeDefinition base ------------------------------------------------------


class _DSLMeta(NodeDefinitionMeta):
    """Compile ``@group`` functions into the existing GroupSpec/NodeType IR."""

    def __new__(mcls, name, bases, namespace, **kw):
        if name == "NodeDefinition":
            return super().__new__(mcls, name, bases, namespace, **kw)

        groups = []
        data_ins, signal_ins, trigger_ins, asset_ins = [], [], [], []
        out_data, out_signal = [], []
        state_defaults = {}

        module_globals = None
        for field, ann in dict(namespace.get("__annotations__", {})).items():
            if isinstance(ann, str):
                if module_globals is None:
                    mod = sys.modules.get(namespace.get("__module__"))
                    module_globals = vars(mod) if mod else {}
                try:
                    ann = eval(ann, module_globals)
                except Exception:
                    continue
            if _is_state(ann):
                state_defaults[field] = namespace.get(field)

        found = False
        for attr, value in list(namespace.items()):
            opts = getattr(value, "_eidolon_group", None)
            if opts is None:
                continue
            found = True
            spec, wrapper, ports = _compile_group(name, value, opts)
            groups.append(spec)
            namespace[attr] = staticmethod(wrapper)
            data_ins.extend(ports["data"])
            signal_ins.extend(ports["signal"])
            trigger_ins.extend(ports["trigger"])
            asset_ins.extend(ports["asset"])
            out_data.extend(ports["out_data"])
            out_signal.extend(ports["out_signal"])

        if found and "groups" in namespace:
            raise DefinitionError(f"{name}: declare groups with @group functions, not a groups attribute")
        namespace["groups"] = tuple(groups)
        namespace["data_in"] = tuple(data_ins)
        namespace["data_out"] = tuple(out_data)
        namespace["trigger_in"] = tuple(trigger_ins)
        namespace["signal_in"] = tuple(signal_ins)
        namespace["signal_out"] = tuple(out_signal)
        namespace["asset_in"] = tuple(asset_ins)
        namespace["state_defaults"] = state_defaults
        namespace.setdefault("init_defaults", {})
        namespace.setdefault("tags", ())
        return super().__new__(mcls, name, bases, namespace, **kw)


class NodeDefinition(metaclass=_DSLMeta):
    """Compile-time declaration base; never instantiate it."""

    TYPE: NodeType

    def __new__(cls, *args, **kwargs):
        raise TypeError("NodeDefinition classes are compile-time declarations; use .TYPE in a graph")
