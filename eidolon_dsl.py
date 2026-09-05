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
  ``-> Signal[bool]`` == signal output; ``@group(outputs=(...))`` and
  ``@group(signals=(...))`` declare multiple data / signal outputs — the
  handler then returns a dict keyed by declared port name (a missing key ==
  no event for that port; a None value is a legal payload emitted as-is)
- ``this`` (optional first parameter) == restricted state view: reads and
  whole-value writes only; in-place mutation of a ``this.x`` value is a
  no-op; undeclared fields raise AttributeError (recorded as KIND_ERROR)
- ``Gated[T, "gate"]`` == data input whose validity a signal level helps
  interpret.  The ``by=`` keyword form is impossible in Python (PEP 637
  subscript kwargs never landed — it is a SyntaxError), so the second
  positional argument is the binding target
- port names are group-qualified: ``"{group}.{param}"`` — ports belong to
  groups, so two groups may declare parameters with the same name
- ``tags`` / ``doc`` are read-only declaration functions (裁定 2026-08-23):
  the base ``NodeDefinition`` declares them with defaults ``()`` / ``None``,
  concrete nodes explicitly override them via ``@staticmethod``; the compiler
  evaluates each once at class creation — class-attribute assignment is a
  compile-time DefinitionError

The compiler reuses the existing ``GroupSpec → NodeType`` pipeline: the
compiled ``NodeType`` is exactly the IR the kernel consumes, and the kernel
has zero knowledge of this front-end.
"""
from __future__ import annotations

import inspect
import sys
from copy import deepcopy
from dataclasses import dataclass, field
from collections.abc import Callable, Mapping
from typing import Annotated, Any, TypeVar, overload, get_type_hints

from eidolon_graph_ref.engine.protocol import GroupOutput
from eidolon_graph_ref.model.assets import AssetIn
from eidolon_graph_ref.model.definition import DefinitionError, GroupSpec, NodeDefinitionMeta
from eidolon_graph_ref.model.node_type import DocSpec, NodeType
from eidolon_graph_ref.model.ports import APPEND, REPLACE, DataIn, DataOut, SignalIn, SignalOut, TriggerIn
from eidolon_graph_ref.model.readiness import ALL, ANY, DATA, TRIGGER, _All, _Any, _Data, _Trigger


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
    config enters the body only through this declared parameter.

    At runtime the handler receives a plain ``dict``; this class exists only as
    a DSL marker.  ``__getitem__`` is declared so Pylance accepts ``cfg["key"]``.
    """

    def __class_getitem__(cls, key: Any) -> Any: ...

    def __getitem__(self, key: str) -> Any: ...





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


# ---- Annotated metadata markers (Python Surface Language) --------------------
# These marker classes live in Annotated[...] metadata so that Python type
# checkers see the *value type* (int, bool, list, ...) while the Eidolon DSL
# compiler extracts the graph-semantic role from the metadata.


@dataclass(frozen=True)
class StateMarker:
    """Annotated[T, StateMarker()] → state field with value type T."""


@dataclass(frozen=True)
class TriggerMarker:
    """Annotated[T, TriggerMarker()] → trigger input, handler receives T."""


@dataclass(frozen=True)
class SignalMarker:
    """Annotated[T, SignalMarker()] → signal input, handler receives T."""


@dataclass(frozen=True)
class GatedMarker:
    """Annotated[T, GatedMarker("gate")] → data input gated by signal."""
    signal: str


@dataclass(frozen=True)
class AppendMarker:
    """Annotated[list[T], AppendMarker()] → cumulative input (cache=APPEND)."""


@dataclass(frozen=True)
class AssetMarker:
    """Annotated[T, AssetMarker()] → asset dependency (build-time resolved)."""
    asset_type: type | None = None



def _is_state(ann: Any) -> bool:
    """Detect state field from both old (State[int]) and new (Annotated[int, StateMarker()]) forms."""
    meta = getattr(ann, "__metadata__", None)
    return (
        ann is State
        or (isinstance(ann, _Marker) and ann.args[0] == "state")
        or (meta is not None and any(isinstance(m, StateMarker) for m in meta))
    )


# ---- @group decorator --------------------------------------------------------

_F = TypeVar("_F", bound=Callable[..., Any])


@dataclass(frozen=True)
class _GroupOpts:
    readiness: Any = None
    defaults: dict = field(default_factory=dict)
    outputs: tuple = ()
    signals: tuple = ()
    trigger: str | None = None


@overload
def group(fn: _F) -> _F: ...
@overload
def group(
    fn: None = ...,
    *,
    readiness: Any = ...,
    defaults: dict[str, Any] | None = ...,
    outputs: tuple[str, ...] = ...,
    signals: tuple[str, ...] = ...,
    trigger: str | None = ...,
) -> Callable[[_F], _F]: ...


def group(fn=None, *, readiness=None, defaults=None, outputs=(), signals=(), trigger=None):
    """Declare the decorated function as an Eidolon Group.

    Decoration only tags the function for the ``NodeDefinition`` metaclass;
    the function is never invoked as a Python method at runtime.

    Type contract: identity decorator — ``Callable[P, R] → Callable[P, R]``.
    Pylance sees the original function signature unchanged.
    """

    opts = _GroupOpts(
        readiness=readiness,
        defaults=dict(defaults or {}),
        outputs=tuple(outputs or ()),
        signals=tuple(signals or ()),
        trigger=trigger,
    )

    def deco(fn):
        setattr(fn, "_eidolon_group", opts)
        return fn

    return deco if fn is None else deco(fn)


# ---- state proxy -------------------------------------------------------------


class _StateProxy:
    """``this`` — a restricted view of node state inside a group function.

    Reads return the snapshot value; the whole snapshot is written back
    when the group fires, so both whole-value assignment and in-place
    mutation of a read value take effect (裁定 2026-08-23 修订:全量写回).
    """

    def __init__(self, snapshot: dict):
        object.__setattr__(self, "_snapshot", snapshot)

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

    @property
    def snapshot(self) -> dict:
        return object.__getattribute__(self, "_snapshot")


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
    """Get type hints preserving Annotated metadata."""
    try:
        return dict(get_type_hints(fn, include_extras=True))
    except Exception:
        return dict(getattr(fn, "__annotations__", {}))


def _get_role(ann: Any) -> str | None:
    """Extract Eidolon role from Annotated metadata or bare class marker."""
    if ann is None:
        return None
    # New Annotated form: check metadata markers
    meta = getattr(ann, "__metadata__", None)
    if meta is not None:
        for m in meta:
            if isinstance(m, StateMarker):
                return "state"
            if isinstance(m, TriggerMarker):
                return "trigger"
            if isinstance(m, SignalMarker):
                return "signal"
            if isinstance(m, GatedMarker):
                return "gated"
            if isinstance(m, AppendMarker):
                return "append"
            if isinstance(m, AssetMarker):
                return "asset"
    # Old bare-class form (backward compat)
    if ann is Trigger:
        return "trigger"
    if ann is Config:
        return "config"
    if ann is Signal:
        return "signal"
    if isinstance(ann, _Marker):
        kind = ann.args[0]
        return kind if kind in ("state", "gated", "append", "asset") else None
    return None


def _extract_type(ann: Any) -> Any:
    """Extract the inner value type T from Annotated[T, ...] or _Marker forms."""
    if ann is None:
        return Any
    # Annotated[T, ...] → T
    meta = getattr(ann, "__metadata__", None)
    if meta is not None:
        args = getattr(ann, "__args__", None)
        return args[0] if args else Any
    # Old _Marker form: ("gated", int, "gate") → int; ("state", int) → int
    if isinstance(ann, _Marker) and len(ann.args) >= 2:
        return ann.args[1]
    return ann


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
        role = _get_role(ann)
        if role == "trigger":
            params.append(_Param(p.name, "trigger", port))
        elif role == "config":
            params.append(_Param(p.name, "config", ""))
        elif role == "signal":
            params.append(_Param(p.name, "signal", port))
        elif role == "gated":
            # Extract binding: new Annotated form uses GatedMarker.signal,
            # old _Marker form uses ann.args[2]
            meta = getattr(ann, "__metadata__", None)
            if meta is not None:
                binding = next(m.signal for m in meta if isinstance(m, GatedMarker))
            else:
                binding = ann.args[2]  # type: ignore[union-attr]
            if not isinstance(binding, str):
                raise DefinitionError(
                    f"{cls_name}.{gname}: Gated binding must be a string, got {binding!r}"
                )
            params.append(
                _Param(p.name, "gated", port, p.default if has_default else None, has_default, binding)
            )
        elif role == "append":
            params.append(_Param(p.name, "append", port, p.default if has_default else None, has_default))
        elif role == "asset":
            if has_default:
                raise DefinitionError(f"{cls_name}.{gname}: asset parameter {p.name!r} takes no default")
            meta = getattr(ann, "__metadata__", None)
            if meta is not None:
                asset_type = next(m.asset_type for m in meta if isinstance(m, AssetMarker))
            else:
                asset_type = ann.args[1]  # type: ignore[union-attr]
            params.append(_Param(p.name, "asset", port, asset_type=asset_type))
        elif role is not None:
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

    # outputs — 裁定(2026-08-23):声明输出端口数决定 handler 返回形态;
    # 1 个 → 裸值;≥2 → dict(键 = 声明端口名,缺失键 = 该端口本轮无事件,
    # None 值 = 合法载荷照发)。signals= 声明信号输出端口,必须配 outputs=
    # (纯信号输出仍走 ``-> Signal[bool]`` 单输出裸值形态)。
    ret = hints.get("return", inspect.Signature.empty)
    if ret is inspect.Signature.empty or ret is None or ret is type(None):
        out_kind = None
    elif isinstance(ret, _Marker) and ret.args[0] == "signal_out":
        out_kind = "signal"  # old form: -> Signal[bool]
    else:
        meta = getattr(ret, "__metadata__", None)
        if meta is not None and any(isinstance(m, SignalMarker) for m in meta):
            out_kind = "signal"  # new form: -> Annotated[bool, SignalMarker()]
        else:
            out_kind = "data"
    if opts.signals and not opts.outputs:
        raise DefinitionError(f"{cls_name}.{gname}: signals= requires outputs=")
    if opts.outputs:
        if out_kind != "data":
            raise DefinitionError(f"{cls_name}.{gname}: outputs= requires a data return annotation")
        data_names = tuple(f"{gname}.{n}" for n in opts.outputs)  # 裁定 2:所有端口组限定
        signal_names = tuple(f"{gname}.{n}" for n in opts.signals)
        data_keys = tuple(opts.outputs)   # dict 协议键 = 声明成员名(未限定)
        signal_keys = tuple(opts.signals)
    else:
        data_names = (gname,) if out_kind == "data" else ()
        signal_names = (gname,) if out_kind == "signal" else ()
        data_keys, signal_keys = (), ()

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
        outputs=data_names + signal_names,
        defaults=dict(opts.defaults),
        handler=gname,
        readiness=_qualify_readiness(opts.readiness, gname),
    )
    wrapper = _make_wrapper(fn, params, data_names, signal_names, data_keys, signal_keys)
    ports = {
        "data": data_ins,
        "signal": signal_ins,
        "trigger": trigger_ins,
        "asset": asset_ins,
        "out_data": [DataOut(n) for n in data_names],
        "out_signal": [SignalOut(n) for n in signal_names],
    }
    return spec, wrapper, ports


def _qualify_readiness(pred: Any, gname: str) -> Any:
    """Group-qualify leaf ports of a readiness predicate (``DATA("a")`` → ``DATA("add.a")``)."""

    if pred is None:
        return None
    if isinstance(pred, (_Data, _Trigger)):
        if "." in pred.port:
            return pred
        return type(pred)(f"{gname}.{pred.port}")
    if isinstance(pred, (_All, _Any)):
        qualified = tuple(_qualify_readiness(c, gname) for c in pred.conds)
        return type(pred)(tuple(c for c in qualified if c is not None))
    raise DefinitionError(f"unsupported readiness predicate {pred!r}")


def _make_wrapper(fn, params: list[_Param], data_names: tuple, signal_names: tuple, data_keys: tuple, signal_keys: tuple):
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
        total = len(data_names) + len(signal_names)
        if result is not None:
            if total == 0:
                # 「写必须声明」:无输出端口的组返回载荷属违规产出(裁定收紧)。
                raise TypeError(f"group declares no outputs but handler returned {result!r}")
            if total == 1:
                name = data_names[0] if data_names else signal_names[0]
                if data_names:
                    out.data_out[name] = result
                else:
                    out.signal_out[name] = result
            else:
                # dict 返回协议(裁定 2026-08-23):键 = outputs=/signals= 声明
                # 成员名(未限定),编译器映射到组限定端口;缺失键 = 该端口
                # 本轮无事件;None 值 = 合法载荷照发;未知键 = 违规产出。
                if not isinstance(result, dict):
                    raise TypeError(
                        f"group with {total} declared outputs must return a dict "
                        f"mapping declared output names to payloads, got {result!r}"
                    )
                declared = set(data_keys) | set(signal_keys)
                unknown = set(result) - declared
                if unknown:
                    raise TypeError(
                        f"undeclared output name(s) {sorted(unknown)}; "
                        f"declared outputs are {sorted(declared)}"
                    )
                for key, port in zip(data_keys, data_names):
                    if key in result:
                        out.data_out[port] = result[key]
                for key, port in zip(signal_keys, signal_names):
                    if key in result:
                        out.signal_out[port] = result[key]
        if proxy is not None:
            out.state = proxy.snapshot  # 全量写回:整值赋值与原地变异均生效
            # State→Data ownership boundary(裁定 2026-08-23):State 持有对象
            # 不得直接进入 Data Plane——输出与 state 对象同一引用时,输出侧
            # 复制解除 alias;Data Plane 内部保持零拷贝共享。
            owned = tuple(proxy.snapshot.values())
            for name, value in out.data_out.items():
                if any(value is v for v in owned):
                    out.data_out[name] = deepcopy(value)
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
        # tags / doc:只读声明函数(裁定 2026-08-23)。基类 NodeDefinition 声明
        # 默认实现,具体节点以 @staticmethod 显式重载;编译期求值一次,结果
        # 进入 NodeType 元数据。类属性赋值形式编译期拒绝。
        for decl_name, default in (("tags", ()), ("doc", None)):
            decl = namespace.get(decl_name)
            if decl is None:
                namespace[decl_name] = default
                continue
            if isinstance(decl, staticmethod):
                decl = decl.__func__
            if not callable(decl):
                raise DefinitionError(
                    f"{name}: {decl_name} must be a read-only @staticmethod function, got {decl!r}"
                )
            namespace[decl_name] = decl()
        return super().__new__(mcls, name, bases, namespace, **kw)


class NodeDefinition(metaclass=_DSLMeta):
    """Compile-time declaration base; never instantiate it."""

    TYPE: NodeType

    def __new__(cls, *args, **kwargs):
        raise TypeError("NodeDefinition classes are compile-time declarations; use .TYPE in a graph")

    @staticmethod
    def tags() -> tuple[str, ...]:
        """只读声明函数(描述层):域分类 / 宿主约定 tags,编译期求值一次。

        具体节点显式重载;未重载 = 无 tag(编辑器侧落 custom 分类)。
        """
        return ()

    @staticmethod
    def doc() -> DocSpec | None:
        """只读声明函数(描述层):节点说明书,编译期求值一次。

        具体节点显式重载;未重载 = 无说明书(编辑器侧显示占位)。
        """
        return None


# ---- unified compile entry for extension nodes -------------------------------

_DSL_VOCABULARY = {
    "NodeDefinition": NodeDefinition,
    "group": group,
    "State": State,
    "Trigger": Trigger,
    "Config": Config,
    "Signal": Signal,
    "Gated": Gated,
    "Append": Append,
    "Asset": Asset,
    "DATA": DATA,
    "TRIGGER": TRIGGER,
    "ALL": ALL,
    "ANY": ANY,
}


def compile_dsl(source: str, type_name: str) -> NodeType:
    """Compile DSL source into a NodeType — the unified entry for extension nodes.

    The visual editor registers extension nodes the same way it registers the
    official pack: compile DSL source → NodeType → put it in the types dict.
    Port names follow the DSL convention (group-qualified); the editor reads the
    compiled port declarations, so built-in (flat) and extension (group-qualified)
    nodes are handled uniformly.
    """
    namespace: dict[str, Any] = dict(_DSL_VOCABULARY)
    try:
        exec(compile(source, "<eidolon-dsl>", "exec"), namespace)
    except DefinitionError:
        raise
    except Exception as e:
        raise DefinitionError(f"DSL 编译失败: {type(e).__name__}: {e}") from e
    cls = namespace.get(type_name)
    if not (isinstance(cls, type) and issubclass(cls, NodeDefinition) and cls is not NodeDefinition):
        raise DefinitionError(f"DSL 源码未定义 NodeDefinition 子类 {type_name!r}")
    return cls.TYPE
