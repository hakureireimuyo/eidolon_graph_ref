# 中优先级改进方案：端口状态统一 + 不变式化

## 概述

将三套**独立、冗余的端口状态机**统一为单一的**分层设计**，并将构建期决定的属性**不变式化**。

```
现状 (分散):              改进后 (统一):
DataPortState             PortState (基类)
  - value                   - port_type: Literal
  - has_value               - is_wired: bool (不变)
  - pending                 - facts: RuntimeFacts
  - pending_events            - value
  - cache_strategy            - level
  - event_driven              - pending

SignalPortState           + DataPortState (特化)
  - level                     - cache_strategy (REPLACE|APPEND)
  - pending                   - has_value
  - pending_events
                            + SignalPortState (特化)
TriggerPortState            - (facts 中的 level)
  - pending
  - pending_events          + TriggerPortState (特化)
  - payload                   - (facts 中的 pending)
  - has_payload
```

### 改进收益

| 指标 | 现状 | 改进后 |
|-----|------|--------|
| **代码行数** | 150 | 80 |
| **字段冗余** | 9 个 | 0 |
| **不变式维护** | 分散 + 易违反 | 集中 + 强制 |
| **bug 表面积** | 大 | 小 |
| **可读性** | 混乱 | 清晰 |
| **内存占用** | - | -10% |

---

## 问题诊断

### 问题 1：字段冗余与不变式违反

**现状代码：**
```python
@dataclass
class DataPortState:
    cache: str
    value: Any = None
    has_value: bool = False
    pending: bool = False
    pending_events: list[int] = field(default_factory=list)
    event_driven: bool = False    # ← 冗余！

@dataclass
class SignalPortState:
    level: bool | None = None
    pending: bool = False
    pending_events: list[int] = field(default_factory=list)
    # ← event_driven 没有！不一致

@dataclass
class TriggerPortState:
    pending: bool = False
    pending_events: list[int] = field(default_factory=list)
    payload: Any = None
    has_payload: bool = False
    # ← 完全不同的结构
```

**问题：**
1. `has_value` 与 `value is None` 的冗余
2. `event_driven` 只在 DataPortState 中，而实际所有端口都有
3. `pending_events` 都是 `list[int]`，结构重复
4. `level`, `payload` 位置不一致

### 问题 2：不变式分散在多处

**不变式：** "一个端口的 `is_wired` 属性由构建期决定，运行时只读"

**现实：** 这个不变式没有代码上的体现

```python
# engine/instance.py 中初始化
wired = (nid, p.name, SLOT_DATA) in self.in_index
self.data_states[nid][p.name] = DataPortState(
    ..., event_driven=wired  # ← 作为参数传入
)

# engine/executor.py 中修改
def _deliver(...):
    state.event_driven = True  # ← 运行时修改！违反不变式

# engine/node_semantics.py 中查询
if not cls.dynamic(inst, node_id, port):
    # 每次都要重新计算
    return inst.data_states[...].event_driven and signal_active(...)
```

**后果：**
- 易误解：新开发者可能在错误的地方修改 `event_driven`
- 易出 bug：不变式约束不明确，无法静态检查
- 难维护：修改逻辑时容易破坏约束

### 问题 3：类型系统缺陷

**现状：** 三个不同的类，共同字段名相同但含义不同

```python
# 都有 pending，含义不同：
DataPortState.pending = bool  # 是否有新数据
SignalPortState.pending = bool  # 是否有新电平
TriggerPortState.pending = bool  # 是否有新激活

# 代码中无法静态知道是哪种
state: PortState
if state.pending:  # ← 模糊！是哪种 pending？
    ...
```

**后果：**
- IDE 无法精确补全
- 类型检查 (mypy) 难以有效
- 阅读代码时需要上下文推导

---

## 方案设计

### 层次 1：基类与分层

```python
from dataclasses import dataclass, field
from typing import Any, Literal

@dataclass
class RuntimeFacts:
    """Runtime-mutable state snapshot (all types share)."""
    value: Any = None           # DataIn: payload; Signal/Trigger: None
    level: bool | None = None   # Signal: HIGH/LOW; others: None
    pending: bool = False       # All: has_value since last consume

@dataclass
class PortState:
    """Base port state: invariants + generic facts."""
    
    # ========== 不变式：build 期决定，运行时只读 ==========
    port_type: Literal["data", "signal", "trigger"]
    is_wired: bool  # 由 in_index 决定；如 True，触发 dynamic mode
    
    # 仅 DataIn：缓存策略（REPLACE | APPEND）
    cache_strategy: Literal["replace", "append"] | None = None
    
    # ========== 运行时可变状态 ==========
    facts: RuntimeFacts = field(default_factory=RuntimeFacts)
    
    # ========== 事件追踪 ==========
    # 使用 Delivery 直接引用（事件索引优化后）
    pending_deliveries: list['Delivery'] = field(default_factory=list)
    
    # ========== 性质查询（推导，不存储） ==========
    @property
    def is_dynamic(self) -> bool:
        """Port in dynamic mode: wired or has_ever_received_event."""
        return self.is_wired or len(self.pending_deliveries) > 0
    
    @property
    def has_value(self) -> bool:
        """For DataIn: has received or has static default."""
        if self.port_type != "data":
            return False
        return self.facts.value is not None
    
    def receive(self, event: 'Event') -> None:
        """Handle incoming event (polymorphic by port_type)."""
        if self.port_type == "data":
            self._receive_data(event)
        elif self.port_type == "signal":
            self._receive_signal(event)
        elif self.port_type == "trigger":
            self._receive_trigger(event)
    
    def _receive_data(self, event: 'Event') -> None:
        """Data event: append or replace value."""
        if self.cache_strategy == "append":
            if self.facts.value is None:
                self.facts.value = [event.payload]
            elif not isinstance(self.facts.value, list):
                self.facts.value = [self.facts.value, event.payload]
            else:
                self.facts.value.append(event.payload)
        else:  # REPLACE
            self.facts.value = event.payload
        self.facts.pending = True
    
    def _receive_signal(self, event: 'Event') -> None:
        """Signal event: update level."""
        self.facts.level = bool(event.payload)
        self.facts.pending = True
    
    def _receive_trigger(self, event: 'Event') -> None:
        """Trigger event: record payload."""
        self.facts.value = event.payload  # 载荷存在 value 中
        self.facts.pending = True
```

### 层次 2：消除多态

**新方式：** 单一 `PortState` 加上 **slot 类型标签**

```python
# 而非三个类，使用标签
port_state = PortState(
    port_type="data",
    is_wired=True,
    cache_strategy="replace",
)

# 访问时查询标签
if port_state.port_type == "data":
    value = port_state.facts.value
elif port_state.port_type == "signal":
    level = port_state.facts.level

# 或者使用 typed_value() 辅助
def get_data_value(ps: PortState) -> Any:
    assert ps.port_type == "data"
    return ps.facts.value
```

**优点：**
- 类型检查更严格（protocol 可以验证）
- 内存布局一致（caching 友好）
- 无多态开销

### 层次 3：不变式强制

```python
@dataclass(frozen=True)
class PortStateInvariant:
    """Immutable port invariants (build-time decided)."""
    port_type: str
    is_wired: bool
    cache_strategy: str | None  # "replace", "append", or None
    signal_binding: str | None  # For gated ports
    
    def __post_init__(self):
        # 校验不变式
        if self.port_type == "data" and self.cache_strategy is None:
            raise ValueError("data port must specify cache strategy")
        if self.port_type != "data" and self.cache_strategy is not None:
            raise ValueError(f"{self.port_type} port cannot have cache_strategy")

@dataclass
class PortState:
    """Port state = (immutable invariants) + (mutable runtime facts)."""
    
    # 不变式打包为单一对象（frozen dataclass）
    invariants: PortStateInvariant  # ← 一次性设定，不可改
    
    # 运行时状态
    facts: RuntimeFacts = field(default_factory=RuntimeFacts)
    pending_deliveries: list[Delivery] = field(default_factory=list)
    
    # 便捷访问器
    @property
    def port_type(self) -> str:
        return self.invariants.port_type
    
    @property
    def is_wired(self) -> bool:
        return self.invariants.is_wired
    
    @property
    def cache_strategy(self) -> str | None:
        return self.invariants.cache_strategy
```

**优点：**
- 不变式在构造时验证（`frozen=True` 防止后续修改）
- 编译器可分析不变式（static analysis）
- 文档清晰：哪些字段是 build 期决定的

---

## 具体改造

### 第 1 步：定义新结构（1 天）

#### 1.1 新增 `engine/port_state_v2.py`

```python
# 文件：engine/port_state_v2.py

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Literal

@dataclass(frozen=True)
class PortStateInvariant:
    """Immutable port characteristics (build-time)."""
    port_type: Literal["data", "signal", "trigger"]
    is_wired: bool
    cache_strategy: Literal["replace", "append"] | None = None
    
    def __post_init__(self):
        if self.port_type == "data" and self.cache_strategy is None:
            raise ValueError("DataPort requires cache_strategy")
        if self.port_type != "data" and self.cache_strategy is not None:
            raise ValueError(f"{self.port_type} cannot have cache_strategy")

@dataclass
class RuntimeFacts:
    """Mutable runtime state (value + level + pending)."""
    value: Any = None
    level: bool | None = None
    pending: bool = False

@dataclass
class PortState:
    """Unified port state combining invariants + runtime facts."""
    
    # 不变式（一次性，frozen）
    invariants: PortStateInvariant
    
    # 运行时状态
    facts: RuntimeFacts = field(default_factory=RuntimeFacts)
    pending_deliveries: list[Delivery] = field(default_factory=list)
    
    # Properties for invariants (convenience)
    @property
    def port_type(self) -> str:
        return self.invariants.port_type
    
    @property
    def is_wired(self) -> bool:
        return self.invariants.is_wired
    
    @property
    def cache_strategy(self) -> str | None:
        return self.invariants.cache_strategy
    
    # Derived properties
    @property
    def is_dynamic(self) -> bool:
        """Port is in dynamic mode: wired OR has received events."""
        return self.is_wired or len(self.pending_deliveries) > 0
    
    @property
    def has_value(self) -> bool:
        """Only for DataPort: has value."""
        return self.port_type == "data" and self.facts.value is not None
    
    # Polymorphic receive (single method, dispatches by type)
    def receive(self, event: Event) -> None:
        """Process incoming event."""
        if self.port_type == "data":
            self._receive_data(event)
        elif self.port_type == "signal":
            self._receive_signal(event)
        else:  # trigger
            self._receive_trigger(event)
    
    def _receive_data(self, event: Event) -> None:
        if self.cache_strategy == "append":
            if self.facts.value is None:
                self.facts.value = [event.payload]
            elif not isinstance(self.facts.value, list):
                self.facts.value = [self.facts.value, event.payload]
            else:
                self.facts.value.append(event.payload)
        else:
            self.facts.value = event.payload
        self.facts.pending = True
    
    def _receive_signal(self, event: Event) -> None:
        self.facts.level = bool(event.payload)
        self.facts.pending = True
    
    def _receive_trigger(self, event: Event) -> None:
        self.facts.value = event.payload
        self.facts.pending = True
```

#### 1.2 兼容性包装

```python
# 文件：engine/port_state.py (改为兼容性层)

"""Backward compatibility: old API wraps new PortState."""

from engine.port_state_v2 import PortState, PortStateInvariant, RuntimeFacts

# 保持旧名字供过渡期使用
DataPortState = PortState
SignalPortState = PortState
TriggerPortState = PortState

def create_data_port_state(
    cache: str,
    value: Any = None,
    has_value: bool = False,
    **kwargs
) -> PortState:
    """Factory for DataPort (v1 API)."""
    return PortState(
        invariants=PortStateInvariant(
            port_type="data",
            is_wired=kwargs.get('wired', False),
            cache_strategy="append" if cache == APPEND else "replace",
        ),
        facts=RuntimeFacts(value=value),
    )

def create_signal_port_state(**kwargs) -> PortState:
    """Factory for SignalPort (v1 API)."""
    return PortState(
        invariants=PortStateInvariant(port_type="signal", is_wired=False),
        facts=RuntimeFacts(level=None),
    )

# ... 类似的工厂函数
```

### 第 2 步：NodeSemantics 适配（1 天）

#### 2.1 简化 dynamic() 判定

```python
# 旧（复杂）
@classmethod
def dynamic(cls, inst, node_id, port) -> bool:
    state = inst.data_states[node_id][port]
    return state.event_driven and cls.signal_active(inst, node_id, port)

# 新（清晰）
@classmethod
def dynamic(cls, inst, node_id, port) -> bool:
    state = inst.data_states[node_id][port]
    return state.is_dynamic and cls.signal_active(inst, node_id, port)
    # is_dynamic 是属性，自动计算：is_wired or len(pending_deliveries) > 0
```

#### 2.2 统一 receive() 调用

```python
# 旧
if slot == SLOT_DATA:
    inst.data_states[node_id][port].receive(event)
elif slot == SLOT_SIGNAL:
    inst.signal_states[node_id][port].receive(event)
else:
    inst.trigger_states[node_id][port].receive(event)

# 新（都是 PortState，无需判断）
port_state = self._get_port_state(inst, node_id, port, slot)
port_state.receive(event)  # 多态由 PortState.receive() 处理
```

#### 2.3 删除冗余逻辑

```python
# 旧 event_driven 查询和修改逻辑
# → 不再需要！is_dynamic 自动从不变式推导

# 旧 has_value 比较
if state.has_value and state.pending:
    # ...
# → 改为
if state.has_value and state.facts.pending:
    # ...
```

### 第 3 步：构建期适配（1 天）

#### 3.1 初始化统一

```python
# engine/instance.py: _build()

# 旧（三种初始化方式）
self.data_states[nid][p.name] = DataPortState(p.cache, value, not wired, False, [], wired)
self.signal_states[nid][s.name] = SignalPortState()
self.trigger_states[nid][t.name] = TriggerPortState()

# 新（统一工厂）
for p in nt.data_in:
    wired = (nid, p.name, SLOT_DATA) in self.in_index
    value = self.configs[nid]["ports"].get(p.name, p.default)
    value = [] if p.cache == APPEND and value is None else value
    
    self.data_states[nid][p.name] = PortState(
        invariants=PortStateInvariant(
            port_type="data",
            is_wired=wired,
            cache_strategy="append" if p.cache == APPEND else "replace",
        ),
        facts=RuntimeFacts(value=value),
    )

for s in nt.signal_in:
    self.signal_states[nid][s.name] = PortState(
        invariants=PortStateInvariant(port_type="signal", is_wired=False),
        facts=RuntimeFacts(level=None),
    )

for t in nt.trigger_in:
    self.trigger_states[nid][t.name] = PortState(
        invariants=PortStateInvariant(port_type="trigger", is_wired=False),
        facts=RuntimeFacts(),
    )
```

### 第 4 步：测试与验证（1 天）

#### 4.1 单元测试

```python
# tests/test_port_state_unified.py

def test_port_state_invariant_validation():
    """Invariants are validated at construction."""
    with pytest.raises(ValueError):
        PortStateInvariant(port_type="signal", cache_strategy="replace")

def test_is_dynamic_property():
    """is_dynamic derived from is_wired + pending_deliveries."""
    ps = PortState(
        invariants=PortStateInvariant(port_type="data", is_wired=False),
    )
    assert ps.is_dynamic is False
    
    # Add delivery → is_dynamic becomes True
    ps.pending_deliveries.append(mock_delivery)
    assert ps.is_dynamic is True

def test_receive_polymorphism():
    """receive() dispatches correctly by port_type."""
    data_ps = PortState(
        invariants=PortStateInvariant(port_type="data", is_wired=True, cache_strategy="replace"),
    )
    signal_ps = PortState(
        invariants=PortStateInvariant(port_type="signal", is_wired=False),
    )
    
    event_data = Event(..., payload=42)
    event_signal = Event(..., payload=True)
    
    data_ps.receive(event_data)
    assert data_ps.facts.value == 42
    
    signal_ps.receive(event_signal)
    assert signal_ps.facts.level is True

def test_cache_append_behavior():
    """APPEND port accumulates values."""
    ps = PortState(
        invariants=PortStateInvariant(
            port_type="data",
            is_wired=True,
            cache_strategy="append",
        ),
    )
    
    ps.receive(Event(..., payload=1))
    ps.receive(Event(..., payload=2))
    
    assert ps.facts.value == [1, 2]
```

#### 4.2 集成测试

```python
def test_compatibility_with_existing_graphs():
    """New PortState works with existing graph definitions."""
    # 运行所有 89 个语义测试，确保无回归
    pytest tests/ -k "not test_dsl" -v
```

### 第 5 步：迁移与清理（0.5 天）

```python
# 删除旧文件
rm engine/port_state_old.py

# 更新导入
# engine/__init__.py
from .port_state_v2 import PortState, PortStateInvariant, RuntimeFacts

# 删除兼容性包装（待所有代码迁移完成）
```

---

## 性能与内存影响

### 内存优化

**现状：**
```python
class DataPortState:
    cache: str
    value: Any
    has_value: bool  # 冗余
    pending: bool
    pending_events: list  # O(k)
    event_driven: bool  # 冗余

# 每个端口约 48 + k 字节
```

**改进后：**
```python
class PortState:
    invariants: PortStateInvariant  # frozen, 共享
    facts: RuntimeFacts  # 32 字节（固定）
    pending_deliveries: list  # O(k)

# 每个端口约 32 + k 字节
# 加上 invariants 共享 → 净降 ~10-15%
```

### 性能改进

**缓存友好性：**
```
旧：三个不同的类 → 三种不同的内存布局 → 缓存命中率低
新：单一 PortState → 一致的布局 → 缓存友好

期望：内循环性能 ↑ 5-10%（memory-bound 操作）
```

---

## 实施路线

```
Day 1: 新结构定义 + 兼容性包装
  ├─ port_state_v2.py: 新结构
  ├─ port_state.py: 兼容性层
  └─ 单元测试 (test_port_state_unified)

Day 2: NodeSemantics 适配
  ├─ receive() 统一
  ├─ dynamic() 简化
  ├─ 删除冗余逻辑
  └─ 回归测试

Day 3: 构建期适配
  ├─ instance._build() 统一初始化
  ├─ executor._deliver() 适配
  ├─ 集成测试
  └─ 性能基准 (memory profiler)

Day 4: 清理 + 文档
  ├─ 删除旧代码
  ├─ 更新文档
  ├─ 代码审查
  └─ 性能报告
```

---

## 验收标准

### 功能

- [ ] 新 `PortState` 支持所有三种端口类型
- [ ] 不变式强制（frozen invariants）
- [ ] 派生属性计算正确（is_dynamic, has_value）
- [ ] 多态 `receive()` 行为等价
- [ ] 89 个语义测试全绿

### 性能

- [ ] 内存占用 -10% to -15%
- [ ] 内循环性能无退化（benchmark)
- [ ] 缓存友好性提升可观察

### 代码质量

- [ ] 冗余字段删除
- [ ] 不变式明确化
- [ ] 代码行数 150 → 80

---

## 相关文档

- 📘 主设计文档：[ARCHITECTURE_REVIEW.md](./ARCHITECTURE_REVIEW.md)
- 🚀 高优先级 1：[REFACTOR_DSL_COMPILATION.md](./REFACTOR_DSL_COMPILATION.md)
- 🚀 高优先级 2：[REFACTOR_EVENT_INDEXING.md](./REFACTOR_EVENT_INDEXING.md)

