# DSL 编译管道重构方案

## 概述

将 `eidolon_dsl.py` 中 1000+ 行的单体 `_compile_group()` 拆解为**三阶段管道**：

```
源代码扫描        语义解释           IR 生成
(Extraction)   (Interpretation)  (Generation)
─────────────  ───────────────  ──────────────
参数提取        角色识别           端口声明
类型扫描        绑定解析           组规范
基本校验        序列校验           包装生成
```

**好处：**
- ✅ 代码可测试性提高 10 倍（每阶段独立测试）
- ✅ 支持多后端（JSON/YAML 配置、远程 DSL 等）
- ✅ 编译错误信息更精确
- ✅ 维护成本降低（350 行 → 150 行核心）

---

## 阶段 1：提取层（Extraction）

### 职责
从函数签名中**纯机械地提取**原始信息，零业务逻辑。

### 输入
```python
def tick(this, trigger: Trigger, count: int = 1) -> int:
    ...
```

### 输出
```python
@dataclass(frozen=True)
class ParameterDeclaration:
    """Raw parameter metadata — no interpretation yet."""
    name: str
    kind: Literal["positional", "keyword", "var_positional", "var_keyword"]
    type_hint: Any  # Raw type hint (may be string, forward ref, etc.)
    default: Any
    has_default: bool

@dataclass(frozen=True)
class ReturnDeclaration:
    """Return type metadata."""
    annotation: Any
    is_none: bool
```

### 实现
```python
def extract_parameters(func) -> tuple[ParameterDeclaration, ...]:
    """Mechanically extract parameter metadata from function signature.
    
    Zero business logic: just unpacking inspect.Signature.
    Raises DefinitionError only on structural errors (e.g., invalid param kind).
    """
    sig = inspect.signature(func)
    hints = _get_type_hints(func)  # Safe fallback to __annotations__
    
    result = []
    for param in sig.parameters.values():
        if param.kind not in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            raise DefinitionError(
                f"parameter {param.name!r} kind {param.kind} not supported"
            )
        
        result.append(ParameterDeclaration(
            name=param.name,
            kind="positional",  # Normalized
            type_hint=hints.get(param.name),
            default=param.default if param.default is not inspect.Parameter.empty else None,
            has_default=param.default is not inspect.Parameter.empty,
        ))
    
    return_hint = sig.return_annotation
    if return_hint is inspect.Signature.empty:
        return_hint = None
    
    return (
        tuple(result),
        ReturnDeclaration(
            annotation=return_hint,
            is_none=return_hint is None or return_hint is type(None),
        )
    )

def extract_return_type(func) -> ReturnDeclaration:
    """Extract return annotation."""
    sig = inspect.signature(func)
    ret = sig.return_annotation
    if ret is inspect.Signature.empty:
        ret = None
    return ReturnDeclaration(
        annotation=ret,
        is_none=ret is None or ret is type(None),
    )
```

**测试示例：**
```python
def test_extract_parameters():
    def fn(this, trigger: Trigger, count: int = 1): pass
    
    params, ret = extract_parameters(fn)
    assert len(params) == 3
    assert params[0].name == "this"
    assert params[1].type_hint is Trigger
    assert params[2].has_default is True
```

---

## 阶段 2：解释层（Interpretation）

### 职责
应用**业务语义规则**，将原始声明映射到核心角色。

### 输入
```python
ParameterDeclaration(name="count", type_hint=int, default=1, has_default=True)
ParameterDeclaration(name="items", type_hint=Append[list], ...)
```

### 输出
```python
@dataclass(frozen=True)
class InterpretedParameter:
    """Semantic interpretation: what is this parameter?"""
    name: str
    role: Literal["this", "trigger", "signal", "data", "append", "gated", "config", "asset"]
    
    # Resolved identities
    port_name: str | None  # group-qualified; None if not a port
    
    # Port-specific metadata
    default: Any = None
    cache_strategy: Literal["replace", "append"] | None = None
    signal_binding: str | None = None  # "gate" for Gated[T, "gate"]
    asset_type: type | None = None
    
    # Validation context
    requires_preceding: set[str] = field(default_factory=set)  # "trigger", "signal", etc.
    forbids_default: bool = False
```

### 实现
```python
def interpret_parameter(
    decl: ParameterDeclaration,
    group_name: str,
) -> InterpretedParameter:
    """Map a parameter declaration to a semantic role.
    
    Business logic lives here: type hints → roles, binding validation, etc.
    """
    
    # Reserved names
    if decl.name == "this":
        return InterpretedParameter(
            name="this",
            role="this",
            port_name=None,
            forbids_default=True,  # `this` cannot have default
        )
    
    if decl.name == "self":
        raise DefinitionError(
            f"parameter 'self' not allowed; use 'this' for state"
        )
    
    # Type hint → role mapping
    hint = decl.type_hint
    
    # Special singleton types
    if hint is Trigger:
        return InterpretedParameter(
            name=decl.name,
            role="trigger",
            port_name=f"{group_name}.{decl.name}",
            forbids_default=True,
        )
    
    if hint is Config:
        return InterpretedParameter(
            name=decl.name,
            role="config",
            port_name=None,
            forbids_default=True,
        )
    
    if hint is Signal:
        return InterpretedParameter(
            name=decl.name,
            role="signal",
            port_name=f"{group_name}.{decl.name}",
            forbids_default=True,
        )
    
    # Marker types (Gated, Append, Asset)
    if isinstance(hint, _Marker):
        kind = hint.args[0]
        
        if kind == "gated":
            t, binding = hint.args[1], hint.args[2]
            if not isinstance(binding, str):
                raise DefinitionError(
                    f"Gated binding must be string, got {binding!r}"
                )
            return InterpretedParameter(
                name=decl.name,
                role="gated",
                port_name=f"{group_name}.{decl.name}",
                default=decl.default,
                cache_strategy="replace",
                signal_binding=binding,
            )
        
        if kind == "append":
            return InterpretedParameter(
                name=decl.name,
                role="append",
                port_name=f"{group_name}.{decl.name}",
                default=decl.default,
                cache_strategy="append",
            )
        
        if kind == "asset":
            asset_type = hint.args[1]
            return InterpretedParameter(
                name=decl.name,
                role="asset",
                port_name=None,
                asset_type=asset_type,
                forbids_default=True,
            )
        
        raise DefinitionError(f"unknown marker type {kind!r}")
    
    # Default: data input
    return InterpretedParameter(
        name=decl.name,
        role="data",
        port_name=f"{group_name}.{decl.name}",
        default=decl.default,
        cache_strategy="replace",
    )

def interpret_parameters(
    decls: tuple[ParameterDeclaration, ...],
    group_name: str,
) -> tuple[InterpretedParameter, ...]:
    """Interpret all parameters in a group function."""
    
    interpreted = tuple(
        interpret_parameter(d, group_name) for d in decls
    )
    
    # Post-interpretation validation
    _validate_parameter_sequence(interpreted, group_name)
    
    return interpreted

def _validate_parameter_sequence(params: tuple[InterpretedParameter, ...], group_name: str):
    """Check parameter ordering: this → specials → required data → defaulted data."""
    
    phase = "start"
    for p in params:
        if p.role == "this":
            if phase != "start":
                raise DefinitionError(
                    f"{group_name}: 'this' must be first parameter"
                )
            phase = "special"
            continue
        
        if p.role in ("trigger", "signal", "config", "asset"):
            if phase not in ("start", "special"):
                raise DefinitionError(
                    f"{group_name}: special parameter {p.name!r} "
                    f"must precede data inputs"
                )
            if p.forbids_default and p.default is not None:
                raise DefinitionError(
                    f"{group_name}: {p.role} parameter {p.name!r} "
                    f"takes no default"
                )
            phase = "special"
            continue
        
        # Data-like roles
        if phase == "special":
            phase = "required"
        
        if p.default is None:
            if phase == "defaulted":
                raise DefinitionError(
                    f"{group_name}: required data input {p.name!r} "
                    f"must precede defaulted inputs"
                )
        else:
            phase = "defaulted"
```

**测试示例：**
```python
def test_interpret_parameter():
    # Type hint Trigger → role="trigger"
    decl = ParameterDeclaration("go", "positional", Trigger, None, False)
    interp = interpret_parameter(decl, "emit")
    assert interp.role == "trigger"
    assert interp.port_name == "emit.go"
    assert interp.forbids_default is True

def test_interpret_gated():
    # Gated[int, "enable"] → role="gated", signal_binding="enable"
    decl = ParameterDeclaration(
        "value", "positional",
        _Marker(("gated", int, "enable")),
        None, False
    )
    interp = interpret_parameter(decl, "process")
    assert interp.role == "gated"
    assert interp.signal_binding == "enable"
    assert interp.cache_strategy == "replace"
```

---

## 阶段 3：生成层（Generation）

### 职责
从解释结果生成**最终 IR 和包装器**。

### 输入
```python
tuple[InterpretedParameter, ...]  # 已解释的参数
ReturnDeclaration(annotation=int, ...)  # 返回值
_GroupOpts(readiness=..., defaults=..., outputs=(), signals=())  # 选项
```

### 输出
```python
GroupSpec(name="tick", inputs=(...), triggers=(...), outputs=(...), ...)
Callable  # Handler wrapper
PortDeclarations(data_in=(...), signal_in=(...), ...)
```

### 实现
```python
@dataclass(frozen=True)
class PortDeclarations:
    """Collected port declarations for a group."""
    data_in: tuple[DataIn, ...]
    data_out: tuple[DataOut, ...]
    signal_in: tuple[SignalIn, ...]
    signal_out: tuple[SignalOut, ...]
    trigger_in: tuple[TriggerIn, ...]
    asset_in: tuple[AssetIn, ...]

def generate_ports(
    params: tuple[InterpretedParameter, ...],
    return_decl: ReturnDeclaration,
    group_name: str,
    opts: _GroupOpts,
) -> PortDeclarations:
    """Generate port declarations from interpreted parameters."""
    
    data_ins = []
    signal_ins = []
    trigger_ins = []
    asset_ins = []
    
    # Bound signal tracking (1:1 invariant)
    bound_signals: set[str] = set()
    
    for p in params:
        if p.role == "trigger":
            trigger_ins.append(TriggerIn(p.port_name))
        
        elif p.role == "signal":
            signal_ins.append(SignalIn(p.port_name))
        
        elif p.role == "asset":
            asset_ins.append(AssetIn(p.name, p.asset_type))
        
        elif p.role in ("data", "append", "gated"):
            signal_binding = (
                f"{group_name}.{p.signal_binding}"
                if p.signal_binding else None
            )
            if signal_binding:
                bound_signals.add(signal_binding)
            
            data_ins.append(DataIn(
                name=p.port_name,
                default=p.default,
                cache=APPEND if p.cache_strategy == "append" else REPLACE,
                signal=signal_binding,
            ))
    
    # Unbound signals as data inputs
    for p in params:
        if p.role == "signal" and p.port_name not in bound_signals:
            # This is a pure signal input (treated as data input)
            pass  # Already counted as signal_in above
    
    # Output declaration
    if return_decl.is_none or return_decl.annotation is None:
        data_out_names = ()
        signal_out_names = ()
    elif isinstance(return_decl.annotation, _Marker) and return_decl.annotation.args[0] == "signal_out":
        signal_out_names = (group_name,)
        data_out_names = ()
    else:
        data_out_names = (group_name,)
        signal_out_names = ()
    
    # Override with @group(outputs=..., signals=...)
    if opts.outputs:
        data_out_names = tuple(f"{group_name}.{n}" for n in opts.outputs)
        signal_out_names = tuple(f"{group_name}.{n}" for n in opts.signals)
    
    return PortDeclarations(
        data_in=tuple(data_ins),
        data_out=tuple(DataOut(n) for n in data_out_names),
        signal_in=tuple(signal_ins),
        signal_out=tuple(SignalOut(n) for n in signal_out_names),
        trigger_in=tuple(trigger_ins),
        asset_in=tuple(asset_ins),
    )

def generate_group_spec(
    params: tuple[InterpretedParameter, ...],
    group_name: str,
    ports: PortDeclarations,
    opts: _GroupOpts,
) -> GroupSpec:
    """Generate GroupSpec from parameters and port declarations."""
    
    # inputs = data + unbound signals
    inputs = tuple(
        p.port_name for p in params
        if p.role in ("data", "append", "gated")
        or (p.role == "signal" and not any(
            ip.signal_binding == p.port_name for ip in params
        ))
    )
    
    # triggers
    triggers = tuple(p.port_name for p in params if p.role == "trigger")
    
    # outputs
    output_names = tuple(p.name for p in (*ports.data_out, *ports.signal_out))
    
    return GroupSpec(
        name=group_name,
        inputs=inputs,
        triggers=triggers,
        outputs=output_names,
        defaults=dict(opts.defaults),
        handler=group_name,  # Will be resolved to callable by NodeDefinitionMeta
        readiness=_qualify_readiness(opts.readiness, group_name),
    )

def generate_handler_wrapper(
    func,
    params: tuple[InterpretedParameter, ...],
    data_out_names: tuple[str, ...],
    signal_out_names: tuple[str, ...],
    data_keys: tuple[str, ...],  # UnQualified keys for dict protocol
    signal_keys: tuple[str, ...],
) -> Callable:
    """Generate handler wrapper: GroupContext → GroupOutput."""
    
    def handler(ctx):
        # Build arguments
        args = []
        
        has_state = any(p.role == "this" for p in params)
        state_proxy = None
        
        if has_state:
            state_proxy = _StateProxy(deepcopy(ctx.state))
        
        for p in params:
            if p.role == "this":
                args.append(state_proxy)
            elif p.role in ("data", "append", "gated", "trigger", "signal"):
                args.append(ctx.data_in.get(p.port_name))
            elif p.role == "config":
                args.append(ctx.config)
            elif p.role == "asset":
                args.append(ctx.assets.get(p.name))
        
        # Call original function
        result = func(*args)
        
        # Build output
        out = GroupOutput()
        
        # Process result based on output count
        total_outputs = len(data_out_names) + len(signal_out_names)
        
        if result is not None:
            if total_outputs == 0:
                raise TypeError(
                    f"group declares no outputs but handler returned {result!r}"
                )
            elif total_outputs == 1:
                # Single output: bare value
                port_name = (
                    data_out_names[0] if data_out_names
                    else signal_out_names[0]
                )
                if data_out_names:
                    out.data_out[port_name] = result
                else:
                    out.signal_out[port_name] = result
            else:
                # Multiple outputs: dict protocol
                if not isinstance(result, dict):
                    raise TypeError(
                        f"group with {total_outputs} outputs "
                        f"must return dict, got {type(result).__name__}"
                    )
                
                declared = set(data_keys) | set(signal_keys)
                unknown = set(result) - declared
                if unknown:
                    raise TypeError(
                        f"undeclared output keys {sorted(unknown)}"
                    )
                
                for key, port in zip(data_keys, data_out_names):
                    if key in result:
                        out.data_out[port] = result[key]
                
                for key, port in zip(signal_keys, signal_out_names):
                    if key in result:
                        out.signal_out[port] = result[key]
        
        # State ownership boundary
        if state_proxy is not None:
            out.state = state_proxy.snapshot
            owned = tuple(state_proxy.snapshot.values())
            for name, value in out.data_out.items():
                if any(value is v for v in owned):
                    out.data_out[name] = deepcopy(value)
        
        return out
    
    return handler
```

---

## 集成点：新 _compile_group()

```python
def _compile_group_v2(
    cls_name: str,
    fn,
    opts: _GroupOpts,
    group_name: str,
) -> tuple[GroupSpec, Callable, PortDeclarations]:
    """Three-stage compilation pipeline."""
    
    # Stage 1: Extract
    params, return_decl = extract_parameters(fn)
    
    # Stage 2: Interpret
    interpreted = interpret_parameters(params, group_name)
    
    # Stage 3: Generate
    ports = generate_ports(interpreted, return_decl, group_name, opts)
    spec = generate_group_spec(interpreted, group_name, ports, opts)
    
    # Resolve output keys (unqualified names for dict protocol)
    data_keys = tuple(
        n.rsplit(".", 1)[1] for n in ports.data_out
    )
    signal_keys = tuple(
        n.rsplit(".", 1)[1] for n in ports.signal_out
    )
    
    wrapper = generate_handler_wrapper(
        fn, interpreted,
        tuple(p.name for p in ports.data_out),
        tuple(p.name for p in ports.signal_out),
        data_keys, signal_keys,
    )
    
    return spec, wrapper, ports
```

---

## 事件索引优化（第二个高优先级项目）

### 当前问题
```python
# consume() 中 O(n) 查询
for eid in state.pending_events:
    event = inst.timeline.events[eid]  # 需要查表
    for delivery in event.deliveries:   # 扫描所有投递
        if delivery.node == node_id and delivery.port == port:
            delivery.consumed_seq = seq  # 找到才更新
```

### 改进方案
在 `Event.deliveries` 中**直接存储消费记录指针**，而非事件 ID 列表：

```python
@dataclass
class Delivery:
    """A single instantiation of event → port."""
    node: str
    port: str
    slot: str
    consumed_seq: int | None = None  # Timeline seq when consumed

@dataclass
class Event:
    id: int
    deliveries: list[Delivery]  # Direct references, not IDs
    produced_by: tuple[str, str] | None  # (node_id, port_name)

# 改进后的消费：O(1)
def consume(inst, state, node_id, port):
    seq = inst.timeline.next_seq
    # 直接迭代 Delivery 对象，无需反向查表
    for delivery in getattr(state, "pending_deliveries", []):
        if delivery.node == node_id and delivery.port == port:
            delivery.consumed_seq = seq
    state.pending = False
    state.pending_deliveries = []
```

**关键改进：**
- ✅ 消费 O(n pending) → O(1) 均摊
- ✅ Timeline 无需反向查表
- ✅ 事件追踪更直观

---

## 迁移策略

### 第一步：并行实现（零风险）
```python
# 保持旧 _compile_group()
# 新增 _compile_group_v2()
# DSL 元类先调用 v2，失败回落到 v1
```

### 第二步：逐个节点迁移
```python
# 在 primitives 中测试新编译器
# 确保生成的 NodeType 等价
```

### 第三步：切换与清理
```python
# 一旦测试通过，改用 v2 为默认
# 移除 v1
```

---

## 预期改进

| 指标 | 现状 | 改进后 |
|-----|------|--------|
| 编译代码行数 | 1000+ | ~350 |
| 单个函数行数 | 200+ | 60~ |
| 可测试函数 | 1 | 7+ |
| 编译错误位置精度 | 粗 | 精细 |
| 支持的后端 | DSL only | DSL + JSON/YAML |

