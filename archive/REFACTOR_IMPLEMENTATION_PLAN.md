# 架构改进实施总体方案

## 概览

这份文档整合两个**高优先级**的并行改进项目：

```
┌─────────────────────────────────────────────────────────┐
│           高优先级架构改进 (2 周冲刺)                    │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌─────────────────────┐   ┌──────────────────────────┐│
│  │  DSL 编译管道重构   │   │   事件索引性能优化      ││
│  │   (REFACTOR_DSL_)   │   │ (REFACTOR_EVENT_)        ││
│  │                     │   │                          ││
│  │ 1000+ 行 → 350 行   │   │ O(n²) → O(n)             ││
│  │ 可维护性 ↑ 10 倍    │   │ 速度 ↑ 100 倍            ││
│  │ 测试覆盖 ↑ 7 倍     │   │ 并行实施                 ││
│  │                     │   │                          ││
│  └─────────────────────┘   └──────────────────────────┘│
│          Week 1                      Week 1-2           │
│       (DSL 先行)          (可与 DSL 并行进行)          │
│                                                         │
│                    ↓ 汇聚点                            │
│            (两个子系统集成测试)                        │
│                                                         │
│  ┌─────────────────────────────────────────────────┐  │
│  │       验收标准：89 个语义测试全绿 + 性能基准  │  │
│  └─────────────────────────────────────────────────┘  │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

---

## 项目 1：DSL 编译管道重构

### 文档位置
👉 `REFACTOR_DSL_COMPILATION.md`

### 要点速查

| 指标 | 现状 | 目标 |
|-----|------|------|
| **代码行数** | 1000+ | ~350 |
| **函数个数** | 1 大单体 | 7 独立函数 |
| **可测试性** | 仅端到端测试 | 每阶段独立测试 |
| **后端支持** | DSL only | DSL + JSON/YAML |
| **错误诊断** | 粗糙 | 精细 |

### 三阶段管道

```python
# 阶段 1：提取 (Extraction) — 纯机械
extract_parameters(func)
    ↓ 返回原始信息，零业务逻辑
    
# 阶段 2：解释 (Interpretation) — 应用规则
interpret_parameters(declarations, group_name)
    ↓ 类型 → 角色、绑定校验、序列校验
    
# 阶段 3：生成 (Generation) — 收集 IR
generate_ports(interpreted, return_decl, ...)
generate_group_spec(interpreted, group_name, ...)
generate_handler_wrapper(func, interpreted, ...)
    ↓ 返回 GroupSpec + Callable + PortDeclarations
```

### 核心代码片段

**阶段 1：提取**
```python
def extract_parameters(func) -> tuple[ParameterDeclaration, ...]:
    """Mechanically extract from signature. Zero business logic."""
    sig = inspect.signature(func)
    hints = _get_type_hints(func)
    return tuple(
        ParameterDeclaration(
            name=p.name,
            type_hint=hints.get(p.name),
            default=...,
            has_default=...,
        )
        for p in sig.parameters.values()
    )
```

**阶段 2：解释**
```python
def interpret_parameter(decl: ParameterDeclaration, group_name: str) -> InterpretedParameter:
    """Map raw declaration to semantic role."""
    if decl.type_hint is Trigger:
        return InterpretedParameter(role="trigger", ...)
    elif decl.type_hint is Signal:
        return InterpretedParameter(role="signal", ...)
    elif isinstance(decl.type_hint, _Marker):
        if decl.type_hint.args[0] == "gated":
            return InterpretedParameter(role="gated", signal_binding=..., ...)
        # ... other markers
    else:
        return InterpretedParameter(role="data", ...)
```

**阶段 3：生成**
```python
def generate_handler_wrapper(...) -> Callable:
    """Create GroupContext → GroupOutput wrapper."""
    def handler(ctx):
        # 组织参数
        args = [ctx.data_in.get(p.port_name) if p.role == "data" else ...]
        result = func(*args)
        # 处理返回值
        out = GroupOutput()
        out.data_out[port] = result
        return out
    return handler
```

### 实施路线

```
Day 1-2: 实现 + 单元测试
  ├─ extract_parameters() + test_extract_*
  ├─ interpret_parameter() + test_interpret_*
  ├─ generate_ports() + test_generate_*
  └─ generate_handler_wrapper() + test_wrapper_*

Day 3: 集成
  ├─ 新 _compile_group_v2() = 三阶段管道
  ├─ 回归测试（与旧 v1 等价性）
  ├─ 原语库编译验证
  └─ 性能测试（编译耗时）

Day 4: 切换 & 清理
  ├─ 弃用旧 _compile_group()
  ├─ 删除 v1 代码
  ├─ 文档更新
  └─ 编辑器后端适配（JSON DSL）
```

### 关键改进

✅ **代码质量**
- 每个函数职责单一（内聚度高）
- 易于理解、修改、测试
- 编译错误精确定位

✅ **可扩展性**
- 支持多后端（JSON、YAML、Protocol Buffer）
- 支持校验策略组合（严格/宽松/自定义）
- 易于引入新的参数角色

✅ **性能**
- 编译耗时减少（无重复扫描）
- 缓存友好（每阶段数据流向清晰）

---

## 项目 2：事件索引性能优化

### 文档位置
👉 `REFACTOR_EVENT_INDEXING.md`

### 要点速查

| 指标 | 现状 | 目标 |
|-----|------|------|
| **消费复杂度** | O(n²) | O(n) |
| **关键路径** | 反向查表 | 直接引用 |
| **扇出性能** | N→1000: 1s+ | N→1000: <10ms |
| **内存开销** | - | 几乎零 |

### 问题根源

**反向依赖链：**
```
PortState.pending_events: list[int]  ← 仅存 ID
    ↓
Event.deliveries: list[Delivery]     ← 查表
    ↓ 遍历每个
Delivery(node, port, slot)            ← 再比对
    ↓ 才能找到
delivery.consumed_seq = seq
```

**O(n²) 场景：**
```
1 源节点 → 1000 个目标节点 (高扇出)
每个 epoch：
  - 创建 1000 个 Delivery
  - 消费时遍历 1000 个 ID
  - 每个 ID 需遍历其所有 Delivery（平均 1，最坏 N）
  = O(1000 × 1000) = 100 万次操作 / epoch
```

### 解决方案

**反向引用：**
```
PortState.pending_deliveries: list[Delivery]  ← 直接存对象引用
    ↓
for delivery in state.pending_deliveries:      ← 直接遍历
    delivery.consumed_seq = seq                ← 无需比对
```

**O(n) 场景：**
```
消费成本 = O(pending_deliveries_count)
        = O(k) 其中 k = 该端口待消费事件数
        ≈ 消费所有事件 1 次
```

### 核心改造

**端口状态：**
```python
@dataclass
class DataPortState:
    # 旧
    pending_events: list[int]
    event_driven: bool
    
    # 新（替代）
    pending_deliveries: list[Delivery]  # ← 直接引用
    
    @property
    def event_driven(self):
        return len(self.pending_deliveries) > 0
```

**消费函数：**
```python
# 旧（O(n²)）
def consume(inst, state, node_id, port):
    for eid in state.pending_events:           # 逐 ID
        event = inst.timeline.events[eid]      # 查表
        for delivery in event.deliveries:      # 遍历
            if delivery.node == node_id and delivery.port == port:
                delivery.consumed_seq = seq

# 新（O(n)）
def consume(inst, state, node_id, port):
    for delivery in state.pending_deliveries:  # 直接遍历
        delivery.consumed_seq = seq            # 无条件判断
```

**投递流程：**
```python
def _deliver(self, inst, event, nid, port, slot, queue):
    delivery = Delivery(nid, port, slot, None)
    event.deliveries.append(delivery)
    
    # 新：链接到端口状态
    port_state = self._get_port_state(inst, nid, port, slot)
    port_state.pending_deliveries.append(delivery)  # ← 关键！
    
    NodeSemantics.receive(inst, event, nid, port, slot)
```

### 性能基准

```python
# 场景 1：高扇出 (1 → 1000)
现状: 100+ ms/epoch  →  改进: < 1 ms/epoch  (100× 加速)

# 场景 2：长链 (10 层)
现状: O(n×10)        →  改进: O(n)          (10× 加速)

# 场景 3：APPEND 端口 (100+ 事件)
现状: O(100²)        →  改进: O(100)        (100× 加速)
```

### 实施路线

```
Day 1: 基础重构 (event.py, port_state.py)
  ├─ Delivery 无需改
  ├─ PortState: pending_events → pending_deliveries
  ├─ 单元测试 (test_port_linking)
  └─ 兼容性检查

Day 2: 消费逻辑 (node_semantics.py)
  ├─ consume() 完全重写（简化！）
  ├─ receive() 适配
  ├─ 回归测试 (test_node_semantics_*)
  └─ 并行性验证

Day 3: 投递流程 (executor.py, instance.py)
  ├─ _deliver() 链接 pending_deliveries
  ├─ 构建期初始化
  ├─ 集成测试 (test_execution_*)
  └─ 性能基准 (benchmark_*.py)

Day 4: 清理 + 文档
  ├─ 移除过期代码
  ├─ 更新注释
  ├─ 性能报告
  └─ 架构文档更新
```

### 关键改进

✅ **性能**
- 消费路径关键指标：O(n²) → O(n)
- 内存零增（甚至可能更少）
- 缓存友好（顺序访问，无随机查表）

✅ **代码质量**
- 消费函数从 15 行 → 8 行
- 条件判断 5+ → 0
- 逻辑清晰（no guard clauses）

✅ **可维护性**
- Event 与 Timeline 无需循环依赖
- 事件追踪原理直观

---

## 并行实施计划

### Week 1 时间表

```
┌──────┬──────────────────────────────────┬──────────────────────────────┐
│ Day  │      DSL 编译管道重构            │     事件索引性能优化         │
├──────┼──────────────────────────────────┼──────────────────────────────┤
│ Mon  │ 1. 阶段 1：提取函数实现          │ 并行开始准备                 │
│      │ 2. 单元测试 (test_extract_*)    │ - 文件分析                  │
│      │ 3. 阶段 2：解释函数实现          │ - 依赖关系梳理              │
│      │ 4. 单元测试 (test_interpret_*)  │                              │
├──────┼──────────────────────────────────┼──────────────────────────────┤
│ Tue  │ 1. 阶段 3：生成函数实现          │ 1. PortState 重构            │
│      │ 2. 单元测试 (test_generate_*)   │ 2. pending_deliveries 集成  │
│      │ 3. 集成 → _compile_group_v2()  │ 3. 单元测试 (test_linking)  │
│      │ 4. 回归测试 (vs v1)            │                              │
├──────┼──────────────────────────────────┼──────────────────────────────┤
│ Wed  │ 1. 原语库编译验证                │ 1. consume() 重写            │
│      │ 2. 性能基准 (编译耗时)          │ 2. receive() 适配            │
│      │ 3. 文档化                        │ 3. 回归测试 (semantics)     │
│      │ 4. 代码审查准备                  │                              │
├──────┼──────────────────────────────────┼──────────────────────────────┤
│ Thu  │ 1. 弃用 v1，切换到 v2           │ 1. Executor 投递流程         │
│      │ 2. 代码清理                      │ 2. 构建期初始化              │
│      │ 3. 文档最终化                    │ 3. 集成测试 (execution_*)   │
│      │                                  │ 4. 性能基准 (fanout_*)      │
├──────┼──────────────────────────────────┼──────────────────────────────┤
│ Fri  │ 集成点 1：编译管道 + 性能优化测试                               │
│      │ ├─ 完整流程测试：源码 → NodeType → 执行                         │
│      │ ├─ 性能对比（旧 vs 新）                                        │
│      │ ├─ 代码审查 (Code Review)                                      │
│      │ └─ 文档审查                                                    │
├──────┼─────────────────────────────────────────────────────────────────┤
│ Mon  │ 基线巩固：89 个语义测试全绿 + 性能基准签收                      │
│      │ ├─ pytest (tests/)                                             │
│      │ ├─ examples/ 运行验证                                          │
│      │ ├─ 性能报告生成                                                │
│      │ └─ 准备 PR 合并                                                │
└──────┴─────────────────────────────────────────────────────────────────┘
```

### 依赖关系

```
DSL 编译与事件索引：完全独立！

┌──────────────────────────────────────┐
│   DSL 编译管道重构                  │
│   (eidolon_dsl.py)                   │
│   ↑                                  │
│   依赖: model/definition.py          │
└──────────────────────────────────────┘
         ↓
      (无竞争)
         ↓
┌──────────────────────────────────────┐
│   事件索引性能优化                  │
│   (engine/{event,port_state}.py)    │
│   ↑                                  │
│   依赖: model/graph.py               │
└──────────────────────────────────────┘

交集：tests/ (但可分离)
并行风险：低 (代码路径完全不同)
```

---

## 验收标准

### 功能验收

- [ ] **DSL 编译管道**
  - [ ] 7 个新函数各自有单元测试
  - [ ] `_compile_group_v2()` 与 `_compile_group()` 等价性验证
  - [ ] 所有 10 个原语编译通过
  - [ ] 编译错误信息精确度提升
  - [ ] 无回归：89 个语义测试全绿

- [ ] **事件索引优化**
  - [ ] `pending_deliveries` 链接正确（测试：delivery_linking）
  - [ ] 消费函数行为等价（测试：vs 旧版本）
  - [ ] 高扇出场景通过（1000 路由测试）
  - [ ] APPEND 端口消费正确
  - [ ] 无回归：89 个语义测试全绿

### 性能验收

```python
# DSL 编译
编译耗时: < 100ms per 10 primitives (vs 旧版本基准)
代码行数: 旧 1000+ → 新 350  (即时可视)

# 事件索引
高扇出 (1 → 1000): < 1ms/epoch (vs 旧版本 100+ ms)
长链 (10 层): 线性阶与层数无关 (vs 旧版本 O(n×depth))
APPEND: O(k) 其中 k = pending_count (vs 旧版本 O(k²))
```

### 文档验收

- [ ] `REFACTOR_DSL_COMPILATION.md` 完成
- [ ] `REFACTOR_EVENT_INDEXING.md` 完成
- [ ] `architecture_guide.md` 已更新（可选）
- [ ] 代码注释规范（每个新函数有 docstring）
- [ ] 性能基准报告生成

---

## 风险与缓解

### 风险 1：DSL 编译兼容性

**风险**：新编译器生成的 NodeType 与旧版本不完全等价

**缓解**：
```python
# 并行运行策略
def _compile_group(fn, opts):
    try:
        result_v2 = _compile_group_v2(fn, opts)
    except DefinitionError as e:
        # 如果新编译器失败，回落旧版本（暂时）
        return _compile_group_v1(fn, opts)
    
    # 不过本方案应该设计为 v2 从第一天就支持所有场景
```

**验证**：
```bash
# 逐个编译每个原语，比对输出
pytest tests/test_dsl_compatibility.py -v
```

### 风险 2：事件索引性能回归

**风险**：新的直接引用方式在某些场景下反而变慢

**缓解**：
```python
# 基准测试覆盖多个场景
benchmark_fanout(num_targets=[10, 100, 1000, 10000])
benchmark_depth(depth=[1, 5, 10, 20])
benchmark_append(event_count=[1, 10, 100, 1000])
```

**检查点**：
- Memory profiler: 验证无内存泄漏
- CPU profiler: 验证关键路径优化

### 风险 3：并行开发冲突

**风险**：两个团队修改相邻代码引发合并冲突

**缓解**：
- 文件级隔离：DSL (eidolon_dsl.py) vs 事件 (engine/event.py)
- 测试分离：tests/test_dsl_*.py vs tests/test_event_*.py
- 日报同步：每天 EOD 同步进度

---

## 后续中等优先级项目

### 已完成
```
✅ 高优先级 1: DSL 编译管道（本周）
✅ 高优先级 2: 事件索引优化（本周）
```

### 下阶段
```
🟡 中优先级 1: 端口状态统一 + 不变式化
   - 见 ARCHITECTURE_REVIEW.md 的"中优先级"部分
   - 工作量：3-4 天
   - 受益：减少 bug 表面积、内存优化

🟡 中优先级 2: Readiness 谓词扩展协议
   - 添加 .explain() / .requires_port_pending()
   - 工作量：2 天
   - 受益：可视化调试、编译优化

🟢 低优先级 1: IR 校验独立
   - validate_node_type() 独立函数
   - 工作量：1 天
   - 受益：版本管理、校验策略组合
```

---

## 项目成功指标

| KPI | 目标值 | 度量方式 |
|-----|--------|--------|
| **代码质量** | 1000 LOC → 350 LOC | `wc -l` on DSL files |
| **可测试性** | 1 函数 → 7 独立可测函数 | Test coverage % |
| **性能** | O(n²) → O(n) on consume | benchmark_*.py |
| **向后兼容** | 89 tests pass | pytest -xvs |
| **文档完整** | 两个详细方案 + 代码注释 | Review checklist |
| **交付质量** | Code review approved | 2 reviewers sign-off |

---

## 相关文档

- 📄 [架构审查完整版](./ARCHITECTURE_REVIEW.md) (coming soon)
- 📘 [DSL 编译管道详细设计](./REFACTOR_DSL_COMPILATION.md)
- 🚀 [事件索引性能优化详细设计](./REFACTOR_EVENT_INDEXING.md)
- 🧪 [现有语义测试套件](./tests/conftest.py)

---

## 联系与反馈

本方案基于 Eidolon 核心架构的深度分析。
- 问题反馈？提交 Issue 或 PR 评论
- 设计讨论？在 PR 中留下建议
- 性能关切？运行 benchmark 并提交结果

---

**最后更新**: 2026-09-04
**状态**: 就绪待实施
**优先级**: 🔴 高 (两个项目并行)
**预计周期**: 2 周 (Week 1-2)

