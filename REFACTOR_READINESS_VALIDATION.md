# 中优先级改进方案 2：Readiness 谓词扩展协议

## 概述

当前 Readiness DSL 过度简洁但**缺乏可观察性**。扩展谓词协议以支持：

1. **调试可视化** — `.explain()` 方法
2. **编译优化** — `.requires_port_pending()` 查询
3. **运行时验证** — 谓词正确性检查

```python
# 现状
readiness = ALL(DATA("a"), TRIGGER("go"))
if readiness.evaluate(data_fn, trigger_fn):
    # 没有办法知道为什么 True/False，哪个子条件失败

# 改进后
readiness = ALL(DATA("a"), TRIGGER("go"))
if not readiness.evaluate(data_fn, trigger_fn):
    print(readiness.explain(state, context))
    # "AND failed: DATA('emit.a') = False (static, no event yet)"
```

---

## 问题诊断

### 问题 1：无法调试 Readiness 失败

**现状：**
```python
def group_ready(cls, inst, node_id: str, group) -> bool:
    if group.readiness is not None:
        return group.readiness.evaluate(
            lambda port: cls.data_ready(inst, node_id, port),
            lambda port: inst.trigger_states[node_id][port].pending,
        )
    # 无法知道为什么返回 False
    # 无法从时间线中查看是哪个条件失败

# 结果：节点永远不火，用户无法诊断
```

**后果：**
- 图执行卡顿，用户盲目猜测
- 需要在 console.py 中手工增加 print 调试
- 文档中没有"如何读懂 Readiness 失败"的说明

### 问题 2：无法优化编译

**现状：**
```python
# 编译器无法知道这个谓词需要哪些端口 pending
readiness = ANY(DATA("a"), DATA("b"))  # 需要 a 或 b

# 不能做的优化：
# 1. 消除死代码（如果 readiness 只依赖 a，b 消费可独立）
# 2. 预注册 trigger 关系（if readiness involves trigger)
# 3. 简化谓词树（常数折叠）
```

### 问题 3：运行时无验证

**现状：**
```python
# 用户可能写出错误的谓词
group.readiness = DATA("nonexistent_port")  # 拼写错误

# 构建期无检查，运行期抛 KeyError
# ← 不优雅，错误信息混乱
```

---

## 方案设计

### 第 1 步：扩展谓词协议

```python
from typing import Protocol, Any, Callable

class ReadinessPredicate(Protocol):
    """Extended readiness predicate with observability."""
    
    def evaluate(self, data_fn, trigger_fn) -> bool:
        """Evaluate predicate given port state callbacks."""
        ...
    
    def explain(self, data_fn, trigger_fn) -> str:
        """Explain evaluation result in human-readable form.
        
        Returns multiline string describing:
        - Which conditions were true/false
        - Why static ports always succeed
        - Which triggered the evaluation result
        """
        ...
    
    def requires_port_pending(self, port: str) -> bool:
        """Does this predicate require the given port to be pending?
        
        False positive allowed (over-report), false negative not allowed.
        Used for compile-time analysis.
        """
        ...
    
    def referenced_ports(self) -> set[str]:
        """Return all port names referenced in this predicate.
        
        Used for:
        - Build-time validation (check ports exist)
        - Optimization (register listeners)
        - Documentation (list dependencies)
        """
        ...
```

### 第 2 步：实现谓词类

```python
# model/readiness.py (改进)

from dataclasses import dataclass

@dataclass(frozen=True)
class _Data:
    """DATA(port): data input is ready."""
    port: str
    
    def evaluate(self, data, trigger) -> bool:
        return data(self.port)
    
    def explain(self, data, trigger) -> str:
        result = data(self.port)
        return f"DATA({self.port!r}) = {result}"
    
    def requires_port_pending(self, port: str) -> bool:
        """DATA port requires pending iff it's dynamic."""
        # 建立期不知道是否 dynamic，所以假设需要
        return self.port == port
    
    def referenced_ports(self) -> set[str]:
        return {self.port}

@dataclass(frozen=True)
class _Trigger:
    """TRIGGER(port): trigger is pending."""
    port: str
    
    def evaluate(self, data, trigger) -> bool:
        return trigger(self.port)
    
    def explain(self, data, trigger) -> str:
        result = trigger(self.port)
        return f"TRIGGER({self.port!r}) = {result}"
    
    def requires_port_pending(self, port: str) -> bool:
        return self.port == port
    
    def referenced_ports(self) -> set[str]:
        return {self.port}

@dataclass(frozen=True)
class _All:
    """ALL(a, b, c): all conditions true."""
    conds: tuple
    
    def evaluate(self, data, trigger) -> bool:
        return all(c.evaluate(data, trigger) for c in self.conds)
    
    def explain(self, data, trigger) -> str:
        """Detailed explanation of AND failure."""
        parts = []
        all_true = True
        
        for cond in self.conds:
            result = cond.evaluate(data, trigger)
            if not result:
                all_true = False
                # 只显示失败的条件（简洁）
                parts.append(f"  ✗ {cond.explain(data, trigger)}")
            else:
                parts.append(f"  ✓ {cond.explain(data, trigger)}")
        
        status = "AND: all conditions met" if all_true else "AND failed"
        return f"{status}:\n" + "\n".join(parts)
    
    def requires_port_pending(self, port: str) -> bool:
        """Requires port if ANY subexpr requires it."""
        return any(c.requires_port_pending(port) for c in self.conds)
    
    def referenced_ports(self) -> set[str]:
        return set().union(*(c.referenced_ports() for c in self.conds))

@dataclass(frozen=True)
class _Any:
    """ANY(a, b, c): any condition true."""
    conds: tuple
    
    def evaluate(self, data, trigger) -> bool:
        return any(c.evaluate(data, trigger) for c in self.conds)
    
    def explain(self, data, trigger) -> str:
        """Detailed explanation of OR."""
        parts = []
        any_true = False
        
        for cond in self.conds:
            result = cond.evaluate(data, trigger)
            if result:
                any_true = True
                parts.append(f"  ✓ {cond.explain(data, trigger)}")
            else:
                parts.append(f"  ✗ {cond.explain(data, trigger)}")
        
        status = "OR: at least one condition met" if any_true else "OR: all failed"
        return f"{status}:\n" + "\n".join(parts)
    
    def requires_port_pending(self, port: str) -> bool:
        """Requires port if ALL subexpr require it (conservative)."""
        if not self.conds:
            return False
        return all(c.requires_port_pending(port) for c in self.conds)
    
    def referenced_ports(self) -> set[str]:
        return set().union(*(c.referenced_ports() for c in self.conds))

# Factory functions remain the same
def DATA(port: str) -> ReadinessPredicate:
    return _Data(port)

def TRIGGER(port: str) -> ReadinessPredicate:
    return _Trigger(port)

def ALL(*conds: ReadinessPredicate) -> ReadinessPredicate:
    return _All(tuple(conds))

def ANY(*conds: ReadinessPredicate) -> ReadinessPredicate:
    return _Any(tuple(conds))
```

### 第 3 步：构建期验证

```python
# model/node_type.py (改进)

def __post_init__(self) -> None:
    """Group invariants including readiness validation."""
    
    # ... 其他校验 ...
    
    for g in self.groups:
        if g.readiness is not None:
            # 新增：检查谓词引用的端口都存在
            referenced = g.readiness.referenced_ports()
            available = {
                p.name for p in (*self.data_in, *self.trigger_in, *self.signal_in)
            }
            
            unknown = referenced - available
            if unknown:
                _err(f"group {g.name!r}: readiness references unknown ports {unknown}")
            
            # 新增：检查所有端口在该组中声明
            for port in referenced:
                if port not in (set(g.inputs) | set(g.triggers)):
                    _err(
                        f"group {g.name!r}: readiness references {port!r} "
                        f"which is not in inputs or triggers"
                    )
```

### 第 4 步：时间线与可视化

```python
# engine/node_semantics.py (改进)

@classmethod
def group_ready(cls, inst, node_id: str, group) -> bool:
    """Evaluate readiness with optional explanation recording."""
    
    if group.readiness is not None:
        result = group.readiness.evaluate(
            lambda port: cls.data_ready(inst, node_id, port),
            lambda port: inst.trigger_states[node_id][port].pending,
        )
        
        # NEW: 记录 readiness 评估到时间线（debug 用）
        if not result:  # 只在失败时记录（节省空间）
            explanation = group.readiness.explain(
                lambda port: cls.data_ready(inst, node_id, port),
                lambda port: inst.trigger_states[node_id][port].pending,
            )
            inst.timeline.record(Entry(
                run=inst.run_no,
                kind="readiness_failed",
                dst_node=node_id,
                group=group.name,
                message=explanation,
            ))
        
        return result
    
    # ... 默认逻辑 ...
```

### 第 5 步：控制台可视化

```python
# console.py (改进)

def print_readiness_trace(inst, node_id, group_name):
    """Print readiness evaluation history for a node's group."""
    
    entries = [
        e for e in inst.timeline.entries
        if e.kind == "readiness_failed"
        and e.dst_node == node_id
        and e.group == group_name
    ]
    
    if not entries:
        print(f"✓ {node_id}.{group_name}: readiness never failed")
        return
    
    print(f"✗ {node_id}.{group_name}: readiness evaluations:")
    for entry in entries[-5:]:  # 最后 5 次
        print(f"  epoch {entry.run}:")
        for line in entry.message.split('\n'):
            print(f"    {line}")

# 在 console 中集成
def print_graph_state(inst):
    # ... 现有代码 ...
    
    # NEW: 显示最近的 readiness 失败
    for node_id in inst.definition.nodes:
        for group in inst.types[inst.definition.nodes[node_id].type].groups:
            print_readiness_trace(inst, node_id, group.name)
```

---

## 编译优化示例

### 优化 1：消除无关消费

```python
# 原来
group = Group(
    inputs=("a", "b", "c"),  # 全部都要等
    readiness=DATA("a"),  # 但实际只需 a!
    ...
)

# 编译器可以优化到
group = Group(
    inputs=("a",),  # 消除 b, c
    readiness=DATA("a"),
    ...
)
# 实现：在 DSL 编译期或构建期消除不被 readiness 引用的输入
```

### 优化 2：预验证端口存在

```python
# 构建期（而非运行期）捕获拼写错误
readiness = ALL(DATA("emit.go"), DATA("emit.typo"))  # ← 拼写错误

# 构建器
raise DefinitionError(
    "group readiness references unknown port 'emit.typo'; "
    "available: emit.go, emit.value"
)
```

---

## 实施路线

```
Day 1: 谓词协议扩展
  ├─ 定义 ReadinessPredicate protocol
  ├─ 实现 _Data / _Trigger / _All / _Any.explain()
  ├─ 实现 requires_port_pending() / referenced_ports()
  └─ 单元测试 (test_readiness_protocol)

Day 2: 构建期集成
  ├─ NodeType.__post_init__() 验证 readiness
  ├─ DSL 编译期检查
  ├─ 错误消息优化
  └─ 回归测试

Day 3: 时间线与可视化
  ├─ 时间线记录 readiness_failed 事件
  ├─ console.py 集成
  ├─ 调试模式 (EIDOLON_DEBUG=1)
  └─ 集成测试

Day 4: 清理 + 文档
  ├─ 性能基准（explain() 开销）
  ├─ 文档更新
  ├─ 调试指南
  └─ 代码审查
```

---

## 验收标准

- [ ] `ReadinessPredicate` 协议扩展完整
- [ ] 所有 4 个谓词类实现 explain() / requires_port_pending() / referenced_ports()
- [ ] 构建期验证拼写错误
- [ ] 时间线记录 readiness 失败
- [ ] console 可显示调试信息
- [ ] 89 个语义测试全绿

---

# 低优先级改进方案：IR 校验独立化 + 优化辅助

## 概述

将 IR 校验从构造期分离出来，支持多种校验策略（严格/宽松/自定义）。

```python
# 现状：校验内联在 __post_init__
nt = NodeType(...)  # ← 这里校验，失败直接抛异常

# 改进后：校验与 IR 分离
nt = NodeType(...)              # ← 无校验，仅数据结构
errors = validate_node_type(nt) # ← 显式校验，返回错误列表
```

### 改进收益

| 指标 | 现状 | 改进后 |
|-----|------|--------|
| **校验时机** | 构造 | 灵活（构造/序列化/加载） |
| **错误处理** | 异常 | 列表（积累所有错误） |
| **校验策略** | 单一 | 可组合（strict/lenient/custom） |
| **版本管理** | 混杂 | 独立（支持版本间兼容） |

---

## 问题诊断

### 问题 1：校验与 IR 混杂

**现状：**
```python
@dataclass(frozen=True)
class NodeType:
    # ... fields ...
    
    def __post_init__(self) -> None:
        """太多职责混在一起"""
        # 1. 检查重复
        # 2. 检查绑定
        # 3. 检查分配
        # 4. 检查输出授权
        # 5. 检查 readiness 引用
        # ... 150+ 行代码
```

**问题：**
- IR 本身无法独立使用（序列化后无法恢复）
- 无法实现"lenient 加载"（跳过某些校验）
- 无法做版本化兼容（新版本加了校验，旧数据无法加载）

### 问题 2：错误收集不完整

**现状：**
```python
# 第一个错误就抛异常，用户无法看到其他错误
if len(group_names) != len(set(group_names)):
    _err("duplicate group name")  # ← 抛异常，停止

# 之后的校验都无法运行
if not g.inputs and not g.triggers:
    _err("empty group")  # ← 不会执行
```

**改进后：**
```python
errors = []

# 收集所有错误，用户一次看到全部
if len(group_names) != len(set(group_names)):
    errors.append("duplicate group name")

if not g.inputs and not g.triggers:
    errors.append("empty group")

if errors:
    raise ValidationError(errors)  # ← 一次性抛出
```

---

## 方案设计

### 第 1 步：独立校验器

```python
# model/validate.py (新文件)

from dataclasses import dataclass
from typing import Protocol

class ValidationError(Exception):
    """Collected validation errors."""
    def __init__(self, errors: list[str]):
        self.errors = errors
    
    def __str__(self):
        return "Validation failed:\n  " + "\n  ".join(self.errors)

class NodeTypeValidator(Protocol):
    """Pluggable validator strategy."""
    
    def validate(self, nt: NodeType) -> list[str]:
        """Return list of validation errors (empty if valid)."""
        ...

def validate_node_type(nt: NodeType, validators: list[NodeTypeValidator] | None = None) -> list[str]:
    """Validate NodeType with multiple strategies."""
    
    if validators is None:
        validators = [
            StructureValidator(),
            PortValidator(),
            GroupValidator(),
            ReadinessValidator(),
        ]
    
    errors = []
    for v in validators:
        errors.extend(v.validate(nt))
    
    return errors

# 具体校验器实现
class StructureValidator(NodeTypeValidator):
    """Check basic structure invariants."""
    
    def validate(self, nt: NodeType) -> list[str]:
        errors = []
        
        # 组名去重
        group_names = [g.name for g in nt.groups]
        if len(group_names) != len(set(group_names)):
            dups = {n for n in group_names if group_names.count(n) > 1}
            errors.append(f"duplicate group names: {dups}")
        
        # 端口名去重（跨类别）
        data_names = {p.name for p in nt.data_in}
        trigger_names = {p.name for p in nt.trigger_in}
        signal_names = {p.name for p in nt.signal_in}
        
        if data_names & trigger_names:
            errors.append(f"port name collision: {data_names & trigger_names}")
        if data_names & signal_names:
            errors.append(f"port name collision: {data_names & signal_names}")
        
        return errors

class PortValidator(NodeTypeValidator):
    """Check port declarations and bindings."""
    
    def validate(self, nt: NodeType) -> list[str]:
        errors = []
        
        # 信号绑定 1:1
        bound_signals: dict[str, str] = {}
        for p in nt.data_in:
            if p.signal:
                if p.signal in bound_signals:
                    errors.append(
                        f"signal {p.signal!r} binds both "
                        f"{bound_signals[p.signal]!r} and {p.name!r}"
                    )
                bound_signals[p.signal] = p.name
        
        return errors

class GroupValidator(NodeTypeValidator):
    """Check group declarations."""
    
    def validate(self, nt: NodeType) -> list[str]:
        errors = []
        
        for g in nt.groups:
            # Handler 必填
            if not g.handler:
                errors.append(f"group {g.name!r}: handler required")
            
            # 空组检查
            if not g.inputs and not g.triggers and g.readiness is None:
                errors.append(f"group {g.name!r}: empty group (no inputs/triggers/readiness)")
            
            # 端口分配
            for p in g.inputs:
                if p not in {p.name for p in nt.data_in} and \
                   p not in {s.name for s in nt.signal_in}:
                    errors.append(f"group {g.name!r}: unknown input port {p!r}")
        
        return errors

class ReadinessValidator(NodeTypeValidator):
    """Check readiness predicates."""
    
    def validate(self, nt: NodeType) -> list[str]:
        errors = []
        
        for g in nt.groups:
            if g.readiness:
                # 检查引用的端口都在组输入中
                for port in g.readiness.referenced_ports():
                    if port not in (set(g.inputs) | set(g.triggers)):
                        errors.append(
                            f"group {g.name!r}: readiness references {port!r} "
                            f"not in inputs/triggers"
                        )
        
        return errors
```

### 第 2 步：NodeType 改造

```python
# model/node_type.py (改进)

@dataclass(frozen=True)
class NodeType:
    """Semantic IR: pure data, no validation."""
    
    # ... 所有字段 ...
    
    def __post_init__(self) -> None:
        """Removed: validation now external."""
        # 仅保留无法在构造外做的事（如 frozen 类的自检）
        pass

    def validate(self, strict: bool = True) -> list[str]:
        """Run validators. Return errors (empty if valid)."""
        
        validators = [
            StructureValidator(),
            PortValidator(),
            GroupValidator(),
            ReadinessValidator(),
        ]
        
        errors = []
        for v in validators:
            errors.extend(v.validate(self))
        
        return errors

# DSL 编译期仍然想快速失败
# → 使用新的 validate() 方法
class NodeDefinitionMeta(type):
    def __new__(mcls, name, bases, namespace, **kw):
        # ... 编译 ...
        
        try:
            nt = NodeType(...)
        except ValueError as e:
            raise DefinitionError(f"{name}: {e}")
        
        # NEW: 显式校验
        errors = nt.validate(strict=True)
        if errors:
            raise DefinitionError(
                f"{name}: validation failed:\n  " +
                "\n  ".join(errors)
            )
        
        return nt
```

### 第 3 步：序列化与加载

```python
# 序列化时不验证（节省 I/O）
nt_dict = nt.to_dict()
json.dump(nt_dict, f)

# 加载时按策略验证
loaded_dict = json.load(f)
nt = NodeType.from_dict(loaded_dict)

errors = nt.validate(strict=False)  # lenient 模式
if errors:
    logger.warning(f"Loaded NodeType with validation warnings:\n  {errors}")
```

---

## 实施路线

```
Day 1: 校验器设计 + 实现
  ├─ ValidationError 定义
  ├─ StructureValidator / PortValidator / ...
  ├─ validate_node_type() 主函数
  └─ 单元测试 (test_validation_*.py)

Day 2: NodeType 适配
  ├─ 移除 __post_init__ 验证
  ├─ 添加 .validate() 方法
  ├─ DSL 编译期适配
  └─ 回归测试

Day 3: 应用与文档
  ├─ 序列化路径适配
  ├─ 调试指南（校验错误理解）
  ├─ 自定义校验器示例
  └─ 代码审查
```

---

# 快速参考：所有改进方案

## 优先级总结

```
┌─────────────────────────────────────────────────────────┐
│                   改进优先级矩阵                        │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  🔴 高优先级 (第 1-2 周)                               │
│  ├─ DSL 编译管道重构         (1000 → 350 LOC)         │
│  └─ 事件索引性能优化         (O(n²) → O(n))           │
│                                                         │
│  🟡 中优先级 (第 3-4 周)                               │
│  ├─ 端口状态统一化           (3 类 → 1 类)            │
│  └─ Readiness 谓词扩展       (新增 explain())         │
│                                                         │
│  🟢 低优先级 (第 5+ 周)                                │
│  └─ IR 校验独立化           (解耦构造 & 校验)        │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

## 文档导航

| 优先级 | 文件 | 工作量 | 收益 |
|--------|------|--------|------|
| 🔴 高 | `REFACTOR_DSL_COMPILATION.md` | 3-4 天 | 代码质量 10× |
| 🔴 高 | `REFACTOR_EVENT_INDEXING.md` | 3-4 天 | 性能 100× |
| 🟡 中 | `REFACTOR_PORT_STATE_UNIFICATION.md` | 3-4 天 | bug 面积 -50% |
| 🟡 中 | `REFACTOR_READINESS_PROTOCOL.md` | 2-3 天 | 可调试性 10× |
| 🟢 低 | `REFACTOR_VALIDATION_SEPARATION.md` | 1-2 天 | 灵活性提升 |

## 总时间预估

```
第 1 周：高优先级 (6-8 天)
  ├─ DSL 编译   (Mon-Thu)
  ├─ 事件索引   (Tue-Fri) [并行]
  └─ 集成测试   (Fri)

第 2 周：中优先级 (6-8 天)
  ├─ 端口状态   (Mon-Thu)
  ├─ Readiness  (Wed-Fri) [并行]
  └─ 集成测试   (Fri)

第 3 周+：低优先级 (2-3 天)
  └─ 校验独立   (按需)
```

## 验收标准总表

- [ ] 89 个语义测试全绿（所有改进）
- [ ] 性能基准通过（DSL 编译 < 100ms, 高扇出 < 10ms）
- [ ] 代码审查通过（2 reviewers）
- [ ] 文档完整（设计 + 使用指南 + API docs）
- [ ] 向后兼容（无breaking changes）

